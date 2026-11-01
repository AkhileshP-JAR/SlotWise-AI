"""Constrained warehouse slotting optimization engine (OR-Tools CP-SAT + Deterministic Greedy/Swap).

Implements Section 8.6 and Section 16:
- Enforces all physical, thermal, hazard, capacity, and dedication constraints.
- Never overwrites historical assignments (uses versioned PROPOSED / ACTIVE / SUPERSEDED / REJECTED lifecycle).
- Persists immutable OptimizationRun, SlotAssignment, AssignmentReason, ExceptionRecord, and AuditEvent records.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ortools.sat.python import cp_model
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.config import DomainConfig, load_domain_config
from backend.app.models import (
    AssignmentReason,
    ExceptionRecord,
    Location,
    OptimizationRun,
    SlotAssignment,
)
from backend.app.models.enums import (
    VALID_ASSIGNMENT_TRANSITIONS,
    AssignmentStatus,
    ExceptionCategory,
    ExceptionIssueType,
    ExceptionSeverity,
    HazardCode,
    OptimizationRunStatus,
    StoragePolicy,
)
from backend.app.optimization.candidates import (
    CandidateGeneratorService,
    EvaluatedCandidate,
    SKUCandidatePool,
)
from backend.app.services.audit_service import AuditService, utc_now_naive
from backend.app.services.compatibility import CompatibilityService, LocationSpec, SKUSpec
from backend.app.utils.ids import (
    extract_trailing_sequence,
    format_exception_id,
    format_optimization_run_id,
    format_reason_id,
    format_slot_assignment_id,
)


@dataclass
class OptimizationResultSummary:
    """Structured summary of an executed and persisted optimization run."""

    run_id: str
    algorithm_name: str
    warehouse_scope: str
    zone_scope: str | None
    sku_count: int
    assigned_count: int
    unassigned_count: int
    candidate_count: int
    rejected_candidate_count: int
    average_assignment_score: float
    assignment_status_created: str
    execution_duration_ms: float
    unassigned_skus: list[str]


class SlottingOptimizerService:
    """Solves constrained SKU-to-location slotting and manages non-destructive assignment lifecycles."""

    def __init__(self, config: DomainConfig | None = None) -> None:
        self.config = config or load_domain_config()
        self.audit_service = AuditService(reference_year=self.config.dataset.reference_year)

    def run_optimization(
        self,
        session: Session,
        warehouse_id: str | None = None,
        zone_id: str | None = None,
        sku_ids: list[str] | None = None,
        strategy: str = "auto",
        storage_policy_override: str | None = None,
        preserve_warehouse_affinity: bool = True,
        apply_immediately: bool | None = None,
        use_ml_forecasts: bool = False,
        ml_forecaster: Any | None = None,
        dataset_version: str | None = None,
        actor: str = "OPTIMIZER",
    ) -> OptimizationResultSummary:
        """Execute candidate generation, constrained optimization, and full audit persistence."""
        t0 = time.perf_counter()
        now_dt = utc_now_naive()
        year = self.config.dataset.reference_year

        should_apply = (
            apply_immediately
            if apply_immediately is not None
            else self.config.optimization.auto_approve_assignments
        )

        forecast_lookup: dict[str, dict[str, Any]] | None = None
        if use_ml_forecasts:
            if ml_forecaster is None:
                from backend.app.ml.forecaster import DemandForecastingService

                ml_forecaster = DemandForecastingService(config=self.config)
            forecast_lookup = ml_forecaster.get_forecast_lookup(
                session=session,
                auto_train_if_missing=True,
            )

        cand_service = CandidateGeneratorService(
            config=self.config,
            storage_policy_override=storage_policy_override,
        )
        (
            sku_specs,
            metrics_map,
            location_specs,
            sku_current_wh,
            compat_service,
            max_wh_dist,
        ) = cand_service.load_operational_context(
            session=session,
            warehouse_id=warehouse_id,
            zone_id=zone_id,
            sku_ids=sku_ids,
            vacate_in_scope_skus=True,
            ml_forecasts=forecast_lookup,
        )

        # Group locations by warehouse for fast scoped lookup
        locs_by_wh: dict[str, list[LocationSpec]] = {}
        loc_spec_by_id: dict[str, LocationSpec] = {}
        for l_spec in location_specs:
            locs_by_wh.setdefault(l_spec.warehouse_id, []).append(l_spec)
            loc_spec_by_id[l_spec.location_id] = l_spec

        # Expand top_n slightly during solver pool construction so joint capacity conflicts have fallback alternatives
        pool_top_n = max(self.config.optimization.top_n_candidates, 25)
        pools: dict[str, SKUCandidatePool] = {}
        total_feasible_candidates = 0
        total_rejected_candidates = 0

        for s_spec in sku_specs:
            m = metrics_map[s_spec.sku_id]
            curr_wh = sku_current_wh.get(s_spec.sku_id)
            if preserve_warehouse_affinity and curr_wh and curr_wh in locs_by_wh:
                eligible_locs = locs_by_wh[curr_wh]
            else:
                eligible_locs = location_specs

            pool = cand_service.evaluate_candidates_for_sku(
                sku=s_spec,
                metrics=m,
                locations=eligible_locs,
                compat_service=compat_service,
                max_wh_dist=max_wh_dist,
                top_n=pool_top_n,
                max_rejected_samples=5,
            )
            pools[s_spec.sku_id] = pool
            total_feasible_candidates += pool.total_feasible_count
            total_rejected_candidates += pool.total_rejected_count

        # Choose solver algorithm
        norm_strat = strategy.lower().strip()
        if norm_strat == "ortools_cpsat" or (
            norm_strat == "auto" and len(sku_specs) <= self.config.optimization.ortools_max_skus
        ):
            algo_name = "ORTOOLS_CPSAT"
            selected_by_sku = self._solve_with_ortools_cpsat(
                sku_specs=sku_specs,
                pools=pools,
                loc_spec_by_id=loc_spec_by_id,
                compat_service=compat_service,
            )
        else:
            algo_name = "DETERMINISTIC_GREEDY_LOCAL_SWAP"
            selected_by_sku = self._solve_with_greedy_and_local_swap(
                sku_specs=sku_specs,
                metrics_map=metrics_map,
                pools=pools,
                loc_spec_by_id=loc_spec_by_id,
                compat_service=compat_service,
            )

        # Post-solve hard-constraint verification guard (defense in depth)
        self._verify_joint_solution_feasibility(
            selected_by_sku=selected_by_sku,
            sku_specs_map={s.sku_id: s for s in sku_specs},
            loc_spec_by_id=loc_spec_by_id,
            compat_service=compat_service,
        )

        # Allocate next OptimizationRun ID
        max_run_id = session.scalar(select(func.max(OptimizationRun.run_id)))
        run_id = format_optimization_run_id(year, extract_trailing_sequence(max_run_id) + 1)

        unassigned_skus = sorted(s.sku_id for s in sku_specs if s.sku_id not in selected_by_sku)
        scores = [c.total_score for c in selected_by_sku.values()]
        avg_score = round(sum(scores) / len(scores), 4) if scores else 0.0
        success_rate = round(len(selected_by_sku) / len(sku_specs), 4) if sku_specs else 1.0
        duration_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        wh_scope_label = warehouse_id or "ALL"
        ds_ver = dataset_version or f"slotwise-{self.config.dataset.profile}-seed{self.config.project.seed}"

        run_status = (
            OptimizationRunStatus.COMPLETED.value
            if not unassigned_skus
            else (OptimizationRunStatus.PARTIAL.value if selected_by_sku else OptimizationRunStatus.FAILED.value)
        )

        opt_run = OptimizationRun(
            run_id=run_id,
            warehouse_scope=wh_scope_label,
            zone_scope=zone_id,
            input_dataset_version=ds_ver,
            config_version=self.config.project.config_version,
            algorithm_name=algo_name,
            algorithm_version="1.0.0",
            scoring_weights_json=self.config.scoring.weights.model_dump(),
            seed=self.config.project.seed,
            sku_count=len(sku_specs),
            candidate_count=total_feasible_candidates,
            assignment_count=len(selected_by_sku),
            unassigned_sku_count=len(unassigned_skus),
            rejected_candidate_count=total_rejected_candidates,
            exception_count=len(unassigned_skus),
            kpi_results_json={
                "assignment_success_rate": success_rate,
                "assignment_failure_rate": round(1.0 - success_rate, 4),
                "average_assignment_score": avg_score,
                "constraint_violation_count": 0,
                "unassigned_skus": unassigned_skus,
                "demand_source": "ML_FORECAST" if use_ml_forecasts else "HISTORICAL_OBSERVED",
                "forecast_guided_skus": (
                    sum(1 for s in sku_specs if forecast_lookup and s.sku_id in forecast_lookup)
                    if use_ml_forecasts
                    else 0
                ),
            },
            execution_duration_ms=duration_ms,
            run_status=run_status,
            created_at=now_dt,
        )
        session.add(opt_run)
        session.flush()

        # Determine next SlotAssignment sequence and max decision_version per SKU
        max_slot_id = session.scalar(select(func.max(SlotAssignment.slot_assignment_id)))
        next_slot_seq = extract_trailing_sequence(max_slot_id) + 1

        ver_rows = session.execute(
            select(SlotAssignment.sku_id, func.max(SlotAssignment.decision_version)).group_by(
                SlotAssignment.sku_id
            )
        ).all()
        max_ver_by_sku = {str(r[0]): int(r[1]) for r in ver_rows}

        created_assignment_ids: list[str] = []
        created_slot_by_sku: dict[str, str] = {}
        reason_seq = 0

        # Step A: Persist all SlotAssignment rows first and flush so FK references from AssignmentReason succeed
        for sid in sorted(pools.keys()):
            chosen = selected_by_sku.get(sid)
            if chosen is not None:
                created_slot_id = format_slot_assignment_id(year, next_slot_seq)
                next_slot_seq += 1
                created_assignment_ids.append(created_slot_id)
                created_slot_by_sku[sid] = created_slot_id
                new_ver = max_ver_by_sku.get(sid, 0) + 1
                sb = chosen.score_breakdown

                reason_txt = sb.explanation
                if use_ml_forecasts and forecast_lookup and sid in forecast_lookup:
                    fi = forecast_lookup[sid]
                    reason_txt = (
                        f"{reason_txt} [ML Forecast ADV={fi['forecast_daily_demand']:.2f}/day, "
                        f"surge={fi['surge_ratio']:.2f}x, tier={fi['forecast_velocity']}]."
                    )

                slot_row = SlotAssignment(
                    slot_assignment_id=created_slot_id,
                    optimization_run_id=run_id,
                    sku_id=sid,
                    location_id=chosen.location_id,
                    warehouse_id=chosen.warehouse_id,
                    assignment_status=AssignmentStatus.PROPOSED.value,
                    assigned_quantity=chosen.assigned_quantity,
                    allocated_weight_kg=chosen.allocated_weight_kg,
                    allocated_volume_m3=chosen.allocated_volume_m3,
                    assignment_score=sb.total_score,
                    distance_score=sb.distance_score,
                    compatibility_score=sb.compatibility_score,
                    capacity_score=sb.capacity_score,
                    velocity_score=sb.velocity_score,
                    pick_frequency_score=sb.pick_frequency_score,
                    replenishment_score=sb.replenishment_score,
                    effective_from=now_dt,
                    effective_to=None,
                    created_at=now_dt,
                    decision_version=new_ver,
                    decision_reason=reason_txt,
                )
                session.add(slot_row)

        session.flush()

        # Step B: Persist top feasible candidates (up to 5) + rejected sample candidates (up to 3) for auditability
        for sid in sorted(pools.keys()):
            pool = pools[sid]
            chosen = selected_by_sku.get(sid)
            created_slot_id = created_slot_by_sku.get(sid)

            persisted_cands: list[EvaluatedCandidate] = list(pool.top_feasible[:5])
            if chosen is not None and all(c.location_id != chosen.location_id for c in persisted_cands):
                persisted_cands.insert(0, chosen)
            persisted_cands.extend(pool.rejected_samples[:3])

            for cand in persisted_cands:
                reason_seq += 1
                is_sel = chosen is not None and cand.location_id == chosen.location_id
                session.add(
                    AssignmentReason(
                        reason_id=format_reason_id(run_id, reason_seq),
                        optimization_run_id=run_id,
                        slot_assignment_id=created_slot_id if is_sel else None,
                        sku_id=sid,
                        candidate_location_id=cand.location_id,
                        is_selected=is_sel,
                        is_feasible=cand.is_feasible,
                        rank_position=cand.rank_position,
                        total_score=cand.total_score,
                        score_breakdown_json=cand.score_breakdown.to_dict(),
                        hard_constraints_checked_json=cand.hard_constraints_checked,
                        rejection_reasons_json=cand.rejection_reasons,
                        created_at=now_dt,
                    )
                )

        # Record explicit NO_COMPATIBLE_LOCATION exceptions for unassigned SKUs
        max_exc_id = session.scalar(select(func.max(ExceptionRecord.exception_id)))
        next_exc_seq = extract_trailing_sequence(max_exc_id) + 1
        existing_open_unassn = {
            str(r)
            for r in session.scalars(
                select(ExceptionRecord.entity_id).where(
                    ExceptionRecord.issue_type == ExceptionIssueType.NO_COMPATIBLE_LOCATION.value,
                    ExceptionRecord.resolution_status.in_(["OPEN", "IN_REVIEW"]),
                )
            ).all()
        }

        for u_sid in unassigned_skus:
            if u_sid in existing_open_unassn:
                continue
            pool = pools[u_sid]
            top_reasons = sorted(pool.rejection_reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
            reason_summary = ", ".join(f"{k} ({v})" for k, v in top_reasons) or "No eligible active slots"
            exc_id = format_exception_id(year, next_exc_seq)
            next_exc_seq += 1
            session.add(
                ExceptionRecord(
                    exception_id=exc_id,
                    optimization_run_id=run_id,
                    category=ExceptionCategory.OPTIMIZATION_FAILURE.value,
                    issue_type=ExceptionIssueType.NO_COMPATIBLE_LOCATION.value,
                    severity=ExceptionSeverity.HIGH.value,
                    entity_type="SKU",
                    entity_id=u_sid,
                    related_entity_ids_json=[run_id],
                    description=(
                        f"Optimization run {run_id} found 0 feasible slots for {u_sid} across "
                        f"{pool.total_locations_evaluated} locations. Top rejections: {reason_summary}."
                    ),
                    detected_at=now_dt,
                    resolution_status="OPEN",
                    recommended_action=(
                        "Assign SKU to custom bulk floor/external oversize facility or review physical packaging dimensions."
                    ),
                )
            )

        session.flush()

        final_status = AssignmentStatus.PROPOSED.value
        if should_apply and created_assignment_ids:
            self.approve_assignments(
                session=session,
                run_id=run_id,
                assignment_ids=created_assignment_ids,
                actor=actor,
                timestamp=now_dt,
            )
            final_status = AssignmentStatus.ACTIVE.value

        self.audit_service.log_event(
            session=session,
            event_type="OPTIMIZATION_RUN_COMPLETED",
            entity_type="OPTIMIZATION_RUN",
            entity_id=run_id,
            actor=actor,
            previous_state=None,
            new_state={
                "algorithm": algo_name,
                "warehouse_scope": wh_scope_label,
                "assigned_count": len(selected_by_sku),
                "unassigned_count": len(unassigned_skus),
                "assignment_status": final_status,
                "average_score": avg_score,
            },
            timestamp=now_dt,
        )
        session.flush()

        return OptimizationResultSummary(
            run_id=run_id,
            algorithm_name=algo_name,
            warehouse_scope=wh_scope_label,
            zone_scope=zone_id,
            sku_count=len(sku_specs),
            assigned_count=len(selected_by_sku),
            unassigned_count=len(unassigned_skus),
            candidate_count=total_feasible_candidates,
            rejected_candidate_count=total_rejected_candidates,
            average_assignment_score=avg_score,
            assignment_status_created=final_status,
            execution_duration_ms=duration_ms,
            unassigned_skus=unassigned_skus,
        )

    def approve_assignments(
        self,
        session: Session,
        run_id: str,
        assignment_ids: list[str] | None = None,
        actor: str = "OPERATOR",
        timestamp: datetime | None = None,
    ) -> dict[str, Any]:
        """Transition PROPOSED assignments to ACTIVE while superseding prior ACTIVE history non-destructively."""
        now_dt = timestamp or utc_now_naive()
        stmt = select(SlotAssignment).where(
            SlotAssignment.optimization_run_id == run_id,
            SlotAssignment.assignment_status == AssignmentStatus.PROPOSED.value,
        )
        if assignment_ids:
            stmt = stmt.where(SlotAssignment.slot_assignment_id.in_(assignment_ids))
        proposed_rows = session.scalars(stmt.order_by(SlotAssignment.slot_assignment_id)).all()

        if not proposed_rows:
            return {
                "run_id": run_id,
                "approved_count": 0,
                "superseded_count": 0,
                "reconciled_locations": 0,
            }

        for row in proposed_rows:
            curr_state = AssignmentStatus(row.assignment_status)
            if AssignmentStatus.ACTIVE not in VALID_ASSIGNMENT_TRANSITIONS[curr_state]:
                raise ValueError(f"Illegal transition {curr_state} -> ACTIVE for {row.slot_assignment_id}")

        target_sku_ids = {r.sku_id for r in proposed_rows}
        target_dedicated_locs = set()
        for r in proposed_rows:
            loc_obj = session.get(Location, r.location_id)
            effective_pol = self.config.optimization.default_storage_policy
            if loc_obj and (
                loc_obj.storage_policy == StoragePolicy.DEDICATED_SINGLE_SKU.value
                or effective_pol == StoragePolicy.DEDICATED_SINGLE_SKU.value
            ):
                target_dedicated_locs.add(r.location_id)

        # Supersede prior ACTIVE records for the approved SKUs (and any baseline duplicate anomalies on those dedicated slots)
        prior_active = session.scalars(
            select(SlotAssignment).where(
                SlotAssignment.assignment_status == AssignmentStatus.ACTIVE.value,
                (SlotAssignment.sku_id.in_(target_sku_ids))
                | (SlotAssignment.location_id.in_(target_dedicated_locs)),
            )
        ).all()

        touched_loc_ids: set[str] = {r.location_id for r in proposed_rows}
        superseded_count = 0

        for old_row in prior_active:
            touched_loc_ids.add(old_row.location_id)
            old_row.assignment_status = AssignmentStatus.SUPERSEDED.value
            old_row.effective_to = now_dt
            superseded_count += 1

        for new_row in proposed_rows:
            new_row.assignment_status = AssignmentStatus.ACTIVE.value
            new_row.effective_from = now_dt
            new_row.effective_to = None

        session.flush()

        # Reconcile exact Location current_weight_kg and current_volume_m3 across all touched locations
        self._reconcile_location_occupancies(session, touched_loc_ids)

        self.audit_service.log_event(
            session=session,
            event_type="ASSIGNMENTS_APPROVED",
            entity_type="OPTIMIZATION_RUN",
            entity_id=run_id,
            actor=actor,
            previous_state={"status": AssignmentStatus.PROPOSED.value},
            new_state={
                "status": AssignmentStatus.ACTIVE.value,
                "approved_count": len(proposed_rows),
                "superseded_historical_count": superseded_count,
            },
            timestamp=now_dt,
        )
        session.flush()
        return {
            "run_id": run_id,
            "approved_count": len(proposed_rows),
            "superseded_count": superseded_count,
            "reconciled_locations": len(touched_loc_ids),
        }

    def reject_assignments(
        self,
        session: Session,
        run_id: str,
        assignment_ids: list[str] | None = None,
        reason: str = "Rejected by warehouse reviewer.",
        actor: str = "OPERATOR",
    ) -> dict[str, Any]:
        """Transition PROPOSED recommendations to REJECTED while leaving existing ACTIVE assignments intact."""
        now_dt = utc_now_naive()
        stmt = select(SlotAssignment).where(
            SlotAssignment.optimization_run_id == run_id,
            SlotAssignment.assignment_status == AssignmentStatus.PROPOSED.value,
        )
        if assignment_ids:
            stmt = stmt.where(SlotAssignment.slot_assignment_id.in_(assignment_ids))
        rows = session.scalars(stmt.order_by(SlotAssignment.slot_assignment_id)).all()

        for row in rows:
            curr_state = AssignmentStatus(row.assignment_status)
            if AssignmentStatus.REJECTED not in VALID_ASSIGNMENT_TRANSITIONS[curr_state]:
                raise ValueError(f"Illegal transition {curr_state} -> REJECTED for {row.slot_assignment_id}")
            row.assignment_status = AssignmentStatus.REJECTED.value
            row.effective_to = now_dt
            row.decision_reason = f"{row.decision_reason} | REJECTED: {reason}"

        self.audit_service.log_event(
            session=session,
            event_type="ASSIGNMENTS_REJECTED",
            entity_type="OPTIMIZATION_RUN",
            entity_id=run_id,
            actor=actor,
            previous_state={"status": AssignmentStatus.PROPOSED.value},
            new_state={"status": AssignmentStatus.REJECTED.value, "rejected_count": len(rows), "reason": reason},
            timestamp=now_dt,
        )
        session.flush()
        return {"run_id": run_id, "rejected_count": len(rows)}

    @staticmethod
    def _reconcile_location_occupancies(session: Session, location_ids: set[str]) -> None:
        """Recalculate exact Location.current_weight_kg and current_volume_m3 from ACTIVE assignments."""
        if not location_ids:
            return
        active_sums = session.execute(
            select(
                SlotAssignment.location_id,
                func.coalesce(func.sum(SlotAssignment.allocated_weight_kg), 0.0),
                func.coalesce(func.sum(SlotAssignment.allocated_volume_m3), 0.0),
            )
            .where(
                SlotAssignment.location_id.in_(location_ids),
                SlotAssignment.assignment_status == AssignmentStatus.ACTIVE.value,
            )
            .group_by(SlotAssignment.location_id)
        ).all()
        sums_map = {str(r[0]): (round(float(r[1]), 4), round(float(r[2]), 6)) for r in active_sums}

        locs = session.scalars(select(Location).where(Location.location_id.in_(location_ids))).all()
        for loc in locs:
            wt, vol = sums_map.get(loc.location_id, (0.0, 0.0))
            loc.current_weight_kg = wt
            loc.current_volume_m3 = vol

    def _solve_with_ortools_cpsat(
        self,
        sku_specs: list[SKUSpec],
        pools: dict[str, SKUCandidatePool],
        loc_spec_by_id: dict[str, LocationSpec],
        compat_service: CompatibilityService,
    ) -> dict[str, EvaluatedCandidate]:
        """Exact constrained binary integer programming assignment solver using Google OR-Tools CP-SAT."""
        model = cp_model.CpModel()
        x_vars: dict[tuple[str, str], cp_model.IntVar] = {}
        u_vars: dict[str, cp_model.IntVar] = {}
        cand_lookup: dict[tuple[str, str], EvaluatedCandidate] = {}
        loc_to_skus: dict[str, list[str]] = {}
        sku_map = {s.sku_id: s for s in sku_specs}

        obj_terms: list[Any] = []
        unassigned_penalty = 10_000_000

        for s_spec in sku_specs:
            sid = s_spec.sku_id
            u_var = model.new_bool_var(f"unassn_{sid}")
            u_vars[sid] = u_var
            obj_terms.append(-unassigned_penalty * u_var)

            sku_cands = pools[sid].top_feasible
            row_vars: list[cp_model.IntVar] = [u_var]
            for cand in sku_cands:
                lid = cand.location_id
                v = model.new_bool_var(f"x_{sid}_{lid}")
                x_vars[(sid, lid)] = v
                cand_lookup[(sid, lid)] = cand
                loc_to_skus.setdefault(lid, []).append(sid)
                row_vars.append(v)
                scaled_score = int(round(cand.total_score * 10000))
                obj_terms.append(scaled_score * v)

            model.add(sum(row_vars) == 1)

        # Location capacity, dedication, and co-location exclusion constraints
        effective_policy_override = compat_service.storage_policy_override
        for lid, s_list in loc_to_skus.items():
            loc = loc_spec_by_id[lid]
            pol = effective_policy_override or loc.storage_policy
            if pol == StoragePolicy.DEDICATED_SINGLE_SKU.value:
                if loc.co_located_skus:
                    for sid in s_list:
                        model.add(x_vars[(sid, lid)] == 0)
                elif len(s_list) > 1:
                    model.add(sum(x_vars[(sid, lid)] for sid in s_list) <= 1)
            else:
                # Shared slot: allow at most 2 co-located SKUs per bin to prevent mixed-bin picker congestion,
                # and cap joint utilization at 90% so optimization never creates >=92% near-exhaustion warnings.
                rem_slots = max(0, 2 - len(loc.co_located_skus))
                if rem_slots == 0:
                    for sid in s_list:
                        model.add(x_vars[(sid, lid)] == 0)
                    continue
                if len(s_list) > rem_slots:
                    model.add(sum(x_vars[(sid, lid)] for sid in s_list) <= rem_slots)

                safe_cap_ratio = 0.90
                avail_wt_g = max(
                    0, int(math.floor((loc.max_weight_kg * safe_cap_ratio - loc.occupied_weight_kg) * 1000))
                )
                avail_vol_cm3 = max(
                    0, int(math.floor((loc.max_volume_m3 * safe_cap_ratio - loc.occupied_volume_m3) * 1_000_000))
                )

                wt_terms = [
                    int(math.ceil(cand_lookup[(sid, lid)].allocated_weight_kg * 1000)) * x_vars[(sid, lid)]
                    for sid in s_list
                ]
                vol_terms = [
                    int(math.ceil(cand_lookup[(sid, lid)].allocated_volume_m3 * 1_000_000)) * x_vars[(sid, lid)]
                    for sid in s_list
                ]
                model.add(sum(wt_terms) <= avail_wt_g)
                model.add(sum(vol_terms) <= avail_vol_cm3)

                # Pairwise conflict exclusions between candidate SKUs on the same shared slot
                for idx_a in range(len(s_list)):
                    for idx_b in range(idx_a + 1, len(s_list)):
                        sa, sb = s_list[idx_a], s_list[idx_b]
                        ok, _ = compat_service.are_two_skus_compatible(sku_map[sa], sku_map[sb])
                        if not ok:
                            model.add(x_vars[(sa, lid)] + x_vars[(sb, lid)] <= 1)

        model.maximize(sum(obj_terms))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = float(self.config.optimization.ortools_time_limit_seconds)
        solver.parameters.relative_gap_limit = 0.002
        solver.parameters.random_seed = int(self.config.project.seed % 2147483647)
        solver.parameters.num_search_workers = 1  # Strictly deterministic single-worker branch-and-cut
        status = solver.solve(model)

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return {}

        selected: dict[str, EvaluatedCandidate] = {}
        for (sid, lid), var in x_vars.items():
            if solver.value(var) == 1:
                selected[sid] = cand_lookup[(sid, lid)]
        return selected

    def _solve_with_greedy_and_local_swap(
        self,
        sku_specs: list[SKUSpec],
        metrics_map: dict[str, Any],
        pools: dict[str, SKUCandidatePool],
        loc_spec_by_id: dict[str, LocationSpec],
        compat_service: CompatibilityService,
    ) -> dict[str, EvaluatedCandidate]:
        """Scalable deterministic constructive heuristic + 2-way local swap improvement."""
        sku_map = {s.sku_id: s for s in sku_specs}

        # Order SKUs: fewest feasible options (MRV), specialty hazmat/cold, highest velocity, largest volume, sku_id
        def _priority_key(s: SKUSpec) -> tuple[Any, ...]:
            m = metrics_map[s.sku_id]
            feas_cnt = pools[s.sku_id].total_feasible_count
            is_specialty = (
                1 if s.hazard_class != HazardCode.NONE.value or s.temperature_requirement != "AMBIENT" else 0
            )
            return (
                0 if feas_cnt == 0 else (1 if feas_cnt <= 5 else 2),
                -is_specialty,
                -float(m.velocity_percentile),
                -float(s.unit_volume_m3),
                s.sku_id,
            )

        ordered_skus = sorted(sku_specs, key=_priority_key)
        rem_wt: dict[str, float] = {
            lid: loc.max_weight_kg * 0.90 - loc.occupied_weight_kg for lid, loc in loc_spec_by_id.items()
        }
        rem_vol: dict[str, float] = {
            lid: loc.max_volume_m3 * 0.90 - loc.occupied_volume_m3 for lid, loc in loc_spec_by_id.items()
        }
        assigned_on_loc: dict[str, list[str]] = {
            lid: [cs.sku_id for cs in loc.co_located_skus] for lid, loc in loc_spec_by_id.items()
        }
        selected: dict[str, EvaluatedCandidate] = {}
        pol_override = compat_service.storage_policy_override

        for s_spec in ordered_skus:
            sid = s_spec.sku_id
            for cand in pools[sid].top_feasible:
                lid = cand.location_id
                loc = loc_spec_by_id[lid]
                pol = pol_override or loc.storage_policy
                occupants = assigned_on_loc[lid]
                if pol == StoragePolicy.DEDICATED_SINGLE_SKU.value and occupants:
                    continue
                if pol != StoragePolicy.DEDICATED_SINGLE_SKU.value and len(occupants) >= 2:
                    continue
                if cand.allocated_weight_kg > rem_wt[lid] + 1e-5:
                    continue
                if cand.allocated_volume_m3 > rem_vol[lid] + 1e-7:
                    continue
                if any(
                    not compat_service.are_two_skus_compatible(s_spec, sku_map[occ_id])[0]
                    for occ_id in occupants
                    if occ_id in sku_map
                ):
                    continue

                selected[sid] = cand
                rem_wt[lid] -= cand.allocated_weight_kg
                rem_vol[lid] -= cand.allocated_volume_m3
                assigned_on_loc[lid].append(sid)
                break

        # Deterministic 2-way local swap improvement on dedicated single-SKU assignments
        cand_by_pair: dict[tuple[str, str], EvaluatedCandidate] = {
            (sid, c.location_id): c for sid, pool in pools.items() for c in pool.top_feasible
        }
        dedicated_sids = [sid for sid, cand in selected.items() if len(assigned_on_loc[cand.location_id]) == 1]
        max_iters = self.config.optimization.local_swap_max_iterations
        swaps_done = 0

        for i in range(len(dedicated_sids)):
            if swaps_done >= max_iters:
                break
            sid_a = dedicated_sids[i]
            cand_a = selected[sid_a]
            loc_a = cand_a.location_id
            for j in range(i + 1, min(len(dedicated_sids), i + 40)):
                sid_b = dedicated_sids[j]
                cand_b = selected[sid_b]
                loc_b = cand_b.location_id
                if cand_a.warehouse_id != cand_b.warehouse_id:
                    continue
                alt_a = cand_by_pair.get((sid_a, loc_b))
                alt_b = cand_by_pair.get((sid_b, loc_a))
                if alt_a is None or alt_b is None:
                    continue
                if alt_a.total_score + alt_b.total_score > cand_a.total_score + cand_b.total_score + 1e-4:
                    selected[sid_a] = alt_a
                    selected[sid_b] = alt_b
                    cand_a = alt_a
                    loc_a = alt_a.location_id
                    swaps_done += 1
                    if swaps_done >= max_iters:
                        break

        return selected

    @staticmethod
    def _verify_joint_solution_feasibility(
        selected_by_sku: dict[str, EvaluatedCandidate],
        sku_specs_map: dict[str, SKUSpec],
        loc_spec_by_id: dict[str, LocationSpec],
        compat_service: CompatibilityService,
    ) -> None:
        """Assert zero hard-constraint or joint-capacity violations before persisting any assignment."""
        by_loc: dict[str, list[EvaluatedCandidate]] = {}
        for cand in selected_by_sku.values():
            if not cand.is_feasible:
                raise RuntimeError(f"Optimizer selected infeasible candidate {cand.sku_id} -> {cand.location_id}")
            by_loc.setdefault(cand.location_id, []).append(cand)

        pol_override = compat_service.storage_policy_override
        for lid, c_list in by_loc.items():
            loc = loc_spec_by_id[lid]
            pol = pol_override or loc.storage_policy
            if pol == StoragePolicy.DEDICATED_SINGLE_SKU.value and (len(c_list) + len(loc.co_located_skus)) > 1:
                raise RuntimeError(f"Optimizer violated DEDICATED_SINGLE_SKU policy on {lid}")

            tot_wt = loc.occupied_weight_kg + sum(c.allocated_weight_kg for c in c_list)
            tot_vol = loc.occupied_volume_m3 + sum(c.allocated_volume_m3 for c in c_list)
            if tot_wt > loc.max_weight_kg + 1e-3 or tot_vol > loc.max_volume_m3 + 1e-5:
                raise RuntimeError(f"Optimizer exceeded joint weight/volume capacity on {lid}")

            all_skus_here = list(loc.co_located_skus) + [sku_specs_map[c.sku_id] for c in c_list]
            for i in range(len(all_skus_here)):
                for j in range(i + 1, len(all_skus_here)):
                    ok, reasons = compat_service.are_two_skus_compatible(all_skus_here[i], all_skus_here[j])
                    if not ok:
                        raise RuntimeError(f"Optimizer violated co-location rule on {lid}: {reasons}")
