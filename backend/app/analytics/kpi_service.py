"""Operational KPI calculator and read-only Ground-Truth Precision/Recall/F1 evaluator (Sections 10 & 11).

All KPIs are computed directly from persisted database records—never hardcoded.
Ground-truth files are accessed ONLY inside `evaluate_exception_detection_against_ground_truth`
for isolated accuracy benchmarking.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.config import DATA_DIR, DomainConfig, load_domain_config
from backend.app.models import (
    SKU,
    ExceptionRecord,
    Location,
    OptimizationRun,
    Replenishment,
    SKUProfile,
    SlotAssignment,
    Warehouse,
    Zone,
)
from backend.app.models.enums import (
    AssignmentStatus,
    ExceptionResolutionStatus,
    ExceptionSeverity,
    ReplenishmentStatus,
)
from backend.app.simulation.picking_simulator import PickingSimulationService


class AnalyticsKPIService:
    """Computes real-time operational KPIs and isolated ground-truth detection F1 metrics."""

    def __init__(self, config: DomainConfig | None = None) -> None:
        self.config = config or load_domain_config()
        self.picking_sim = PickingSimulationService(self.config)

    def get_overview_kpis(
        self,
        session: Session,
        warehouse_id: str | None = None,
        sample_picking_orders: int = 250,
    ) -> dict[str, Any]:
        """Calculate complete warehouse, SKU, optimization, picking, replenishment, and exception KPIs."""
        # 1. Warehouse & Location KPIs
        wh_stmt = select(Warehouse)
        zone_stmt = select(Zone)
        if warehouse_id:
            wh_stmt = wh_stmt.where(Warehouse.warehouse_id == warehouse_id)
            zone_stmt = zone_stmt.where(Zone.warehouse_id == warehouse_id)
        warehouses = session.scalars(wh_stmt).all()
        zones = session.scalars(zone_stmt).all()
        zone_ids = [z.zone_id for z in zones]

        locs = session.scalars(select(Location).where(Location.zone_id.in_(zone_ids))).all() if zone_ids else []
        total_locs = len(locs)
        occupied_locs = sum(
            1 for loc in locs if float(loc.current_weight_kg) > 0.0 or float(loc.current_volume_m3) > 0.0
        )
        blocked_locs = sum(1 for loc in locs if bool(loc.is_blocked))
        available_locs = max(0, total_locs - occupied_locs - blocked_locs)
        loc_util_pct = round((occupied_locs / total_locs) * 100.0, 2) if total_locs > 0 else 0.0

        tot_max_wt = sum(float(loc.max_weight_kg) for loc in locs)
        tot_curr_wt = sum(float(loc.current_weight_kg) for loc in locs)
        tot_max_vol = sum(float(loc.max_volume_m3) for loc in locs)
        tot_curr_vol = sum(float(loc.current_volume_m3) for loc in locs)

        # 2. SKU & Profile KPIs
        total_skus = int(session.scalar(select(func.count()).select_from(SKU)) or 0)
        profiles = session.scalars(select(SKUProfile).where(SKUProfile.effective_to.is_(None))).all()
        vel_counts = {"FAST": 0, "MEDIUM": 0, "SLOW": 0}
        abc_counts = {"A": 0, "B": 0, "C": 0}
        xyz_counts = {"X": 0, "Y": 0, "Z": 0}
        for p in profiles:
            vel_counts[p.demand_velocity] = vel_counts.get(p.demand_velocity, 0) + 1
            abc_counts[p.abc_class] = abc_counts.get(p.abc_class, 0) + 1
            xyz_counts[p.xyz_class] = xyz_counts.get(p.xyz_class, 0) + 1

        # 3. Optimization & Active Assignment KPIs
        assn_stmt = select(SlotAssignment).where(SlotAssignment.assignment_status == AssignmentStatus.ACTIVE.value)
        if warehouse_id:
            assn_stmt = assn_stmt.where(SlotAssignment.warehouse_id == warehouse_id)
        active_assns = session.scalars(assn_stmt).all()
        avg_assn_score = (
            round(sum(float(a.assignment_score) for a in active_assns) / len(active_assns), 4)
            if active_assns
            else 0.0
        )

        run_stmt = select(OptimizationRun).order_by(
            OptimizationRun.created_at.desc(), OptimizationRun.run_id.desc()
        )
        if warehouse_id:
            run_stmt = run_stmt.where(OptimizationRun.warehouse_scope.in_([warehouse_id, "ALL"]))
        latest_run = session.scalars(run_stmt.limit(1)).first()
        if latest_run is not None:
            opt_kpis = {
                "latest_run_id": latest_run.run_id,
                "algorithm_name": latest_run.algorithm_name,
                "assignment_success_rate": latest_run.kpi_results_json.get("assignment_success_rate", 0.0),
                "assignment_failure_rate": latest_run.kpi_results_json.get("assignment_failure_rate", 0.0),
                "constraint_violation_count": latest_run.kpi_results_json.get("constraint_violation_count", 0),
                "average_assignment_score": latest_run.kpi_results_json.get(
                    "average_assignment_score", avg_assn_score
                ),
                "candidate_slot_availability": available_locs,
                "evaluated_candidate_pairs": latest_run.candidate_count,
                "execution_duration_ms": latest_run.execution_duration_ms,
            }
        else:
            assigned_unique_skus = len({a.sku_id for a in active_assns})
            denom_skus = max(1, total_skus)
            sr = round(assigned_unique_skus / denom_skus, 4)
            opt_kpis = {
                "latest_run_id": None,
                "algorithm_name": "BASELINE_UNOPTIMIZED",
                "assignment_success_rate": sr,
                "assignment_failure_rate": round(1.0 - sr, 4),
                "constraint_violation_count": sum(
                    1
                    for loc in locs
                    if float(loc.current_weight_kg) > float(loc.max_weight_kg) + 1e-4
                    or float(loc.current_volume_m3) > float(loc.max_volume_m3) + 1e-6
                ),
                "average_assignment_score": avg_assn_score,
                "candidate_slot_availability": available_locs,
                "evaluated_candidate_pairs": 0,
                "execution_duration_ms": 0.0,
            }

        # 4. Picking KPIs (Simulated Estimate comparing baseline vs current/latest optimized layout)
        pick_cmp = self.picking_sim.compare_slotting_layouts(
            session=session,
            optimization_run_id=latest_run.run_id if latest_run else None,
            warehouse_id=warehouse_id,
            max_orders=sample_picking_orders,
        )

        # 5. Replenishment KPIs
        rep_stmt = select(Replenishment).where(
            Replenishment.replenishment_status.in_(
                [
                    ReplenishmentStatus.PENDING.value,
                    ReplenishmentStatus.IN_PROGRESS.value,
                    ReplenishmentStatus.OVERLOADED.value,
                ]
            )
        )
        if warehouse_id:
            rep_stmt = rep_stmt.where(Replenishment.warehouse_id == warehouse_id)
        active_reps = session.scalars(rep_stmt).all()
        tot_rep_min = round(sum(float(r.labor_minutes_required) for r in active_reps), 2)
        tot_wh_labor_cap = sum(float(w.daily_labor_minutes_capacity) for w in warehouses) or 1.0
        overloaded_tasks = sum(
            1 for r in active_reps if r.replenishment_status == ReplenishmentStatus.OVERLOADED.value
        )

        # 6. Exception KPIs
        all_excs = session.scalars(select(ExceptionRecord)).all()
        crit_excs = sum(
            1
            for e in all_excs
            if e.severity in {ExceptionSeverity.CRITICAL.value, ExceptionSeverity.HIGH.value}
            and e.resolution_status != ExceptionResolutionStatus.RESOLVED.value
        )
        warn_excs = sum(
            1
            for e in all_excs
            if e.severity in {ExceptionSeverity.MEDIUM.value, ExceptionSeverity.LOW.value}
            and e.resolution_status != ExceptionResolutionStatus.RESOLVED.value
        )
        resolved_excs = sum(
            1
            for e in all_excs
            if e.resolution_status
            in {ExceptionResolutionStatus.RESOLVED.value, ExceptionResolutionStatus.DISMISSED.value}
        )
        open_excs = sum(
            1
            for e in all_excs
            if e.resolution_status
            in {ExceptionResolutionStatus.OPEN.value, ExceptionResolutionStatus.IN_REVIEW.value}
        )

        return {
            "warehouse_scope": warehouse_id or "ALL",
            "warehouse": {
                "total_warehouses": len(warehouses),
                "total_zones": len(zones),
                "total_locations": total_locs,
                "occupied_locations": occupied_locs,
                "available_locations": available_locs,
                "blocked_locations": blocked_locs,
                "location_utilization_pct": loc_util_pct,
                "weight_capacity_utilization_pct": round((tot_curr_wt / max(tot_max_wt, 1.0)) * 100.0, 2),
                "volume_capacity_utilization_pct": round((tot_curr_vol / max(tot_max_vol, 1e-6)) * 100.0, 2),
            },
            "sku": {
                "total_skus": total_skus,
                "fast_movers": vel_counts.get("FAST", 0),
                "medium_movers": vel_counts.get("MEDIUM", 0),
                "slow_movers": vel_counts.get("SLOW", 0),
                "abc_distribution": abc_counts,
                "xyz_distribution": xyz_counts,
            },
            "optimization": opt_kpis,
            "picking_simulated_estimates": {
                "orders_evaluated": pick_cmp["orders_evaluated"],
                "baseline_pick_distance_m": pick_cmp["baseline_distance_m"],
                "optimized_pick_distance_m": pick_cmp["optimized_distance_m"],
                "distance_saved_m": pick_cmp["distance_saved_m"],
                "distance_improvement_pct": pick_cmp["percentage_improvement"],
                "picking_time_saved_seconds": pick_cmp["estimated_time_saved_seconds"],
                "picking_time_improvement_pct": pick_cmp["time_improvement_pct"],
            },
            "replenishment": {
                "replenishment_requests": len(active_reps),
                "total_workload_minutes": tot_rep_min,
                "available_labor_minutes": round(tot_wh_labor_cap, 2),
                "labor_utilization_pct": round((tot_rep_min / tot_wh_labor_cap) * 100.0, 2),
                "overloaded_tasks": overloaded_tasks,
            },
            "exceptions": {
                "total_exceptions": len(all_excs),
                "open_exceptions": open_excs,
                "critical_and_high_open": crit_excs,
                "warning_and_low_open": warn_excs,
                "resolved_exceptions": resolved_excs,
            },
        }

    @staticmethod
    def evaluate_exception_detection_against_ground_truth(
        session: Session,
        profile: str = "small",
        data_root: Path = DATA_DIR,
    ) -> dict[str, Any]:
        """Read-only evaluation comparing detected ExceptionRecords against isolated ground_truth.json."""
        gt_path = data_root / "ground_truth" / profile / "ground_truth.json"
        if not gt_path.exists():
            return {
                "ground_truth_available": False,
                "profile": profile,
                "precision": 0.0,
                "recall": 0.0,
                "f1_score": 0.0,
            }

        gt_payload = json.loads(gt_path.read_text(encoding="utf-8"))
        gt_anomalies = gt_payload.get("anomalies", [])
        expected_set: set[tuple[str, str]] = {
            (str(a["expected_issue_type"]), str(a["entity_id"])) for a in gt_anomalies
        }
        gt_types = {t for t, _ in expected_set}

        detected_records = session.scalars(select(ExceptionRecord)).all()
        # Evaluate across the anomaly issue types benchmarked in ground truth
        detected_set: set[tuple[str, str]] = {
            (str(e.issue_type), str(e.entity_id)) for e in detected_records if e.issue_type in gt_types
        }

        tp_set = detected_set & expected_set
        fp_set = detected_set - expected_set
        fn_set = expected_set - detected_set

        tp = len(tp_set)
        fp = len(fp_set)
        fn = len(fn_set)

        precision = round(tp / (tp + fp), 4) if (tp + fp) > 0 else 0.0
        recall = round(tp / (tp + fn), 4) if (tp + fn) > 0 else 0.0
        f1 = round((2.0 * precision * recall) / (precision + recall), 4) if (precision + recall) > 0 else 0.0

        return {
            "ground_truth_available": True,
            "profile": profile,
            "expected_anomalies": len(expected_set),
            "detected_benchmark_exceptions": len(detected_set),
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "precision": precision,
            "recall": recall,
            "f1_score": f1,
            "unmatched_false_negatives": sorted(
                [{"issue_type": i, "entity_id": e} for i, e in fn_set], key=lambda x: x["entity_id"]
            ),
            "unexpected_false_positives": sorted(
                [{"issue_type": i, "entity_id": e} for i, e in fp_set], key=lambda x: x["entity_id"]
            ),
        }
