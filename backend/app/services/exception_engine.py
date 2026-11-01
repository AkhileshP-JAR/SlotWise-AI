"""Operational, business-rule, data-quality, and optimization exception detection engine (Section 9).

Operates strictly on operational database records without reading any ground-truth files
(enforcing Section 10 Data Leakage Prevention).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.config import DomainConfig, load_domain_config
from backend.app.models import (
    SKU,
    Aisle,
    CompatibilityRule,
    ExceptionRecord,
    Location,
    PickEvent,
    Replenishment,
    SKUProfile,
    SlotAssignment,
    Warehouse,
    Zone,
    ZoneHazardAllowance,
)
from backend.app.models.enums import (
    VALID_EXCEPTION_TRANSITIONS,
    AssignmentStatus,
    EntityStatus,
    ExceptionCategory,
    ExceptionIssueType,
    ExceptionResolutionStatus,
    ExceptionSeverity,
    ReplenishmentStatus,
    SKUStatus,
    StoragePolicy,
)
from backend.app.services.audit_service import AuditService, utc_now_naive
from backend.app.services.compatibility import CompatibilityService, PairwiseConflictRules, SKUSpec
from backend.app.utils.ids import extract_trailing_sequence, format_exception_id


class ExceptionDetectionService:
    """Scans operational warehouse state to detect, persist, and manage lifecycle of exceptions."""

    def __init__(self, config: DomainConfig | None = None) -> None:
        self.config = config or load_domain_config()
        self.audit_service = AuditService(reference_year=self.config.dataset.reference_year)

    def run_detection(
        self,
        session: Session,
        optimization_run_id: str | None = None,
        auto_resolve_remediated: bool = False,
        actor: str = "EXCEPTION_ENGINE",
    ) -> dict[str, Any]:
        """Scan operational tables, persist newly detected exceptions, and optionally resolve remediated ones."""
        now_dt = utc_now_naive()
        year = self.config.dataset.reference_year

        findings = self._scan_operational_findings(session)

        # Load currently open/in-review persisted exceptions for idempotent deduplication
        existing_open = session.scalars(
            select(ExceptionRecord).where(
                ExceptionRecord.resolution_status.in_(
                    [
                        ExceptionResolutionStatus.OPEN.value,
                        ExceptionResolutionStatus.IN_REVIEW.value,
                    ]
                )
            )
        ).all()
        open_by_key: dict[tuple[str, str, str], ExceptionRecord] = {
            (e.issue_type, e.entity_type, e.entity_id): e for e in existing_open
        }

        max_exc_id = session.scalar(select(func.max(ExceptionRecord.exception_id)))
        next_seq = extract_trailing_sequence(max_exc_id) + 1

        newly_created: list[ExceptionRecord] = []
        active_finding_keys: set[tuple[str, str, str]] = set()

        for f in findings:
            key = (f["issue_type"], f["entity_type"], f["entity_id"])
            active_finding_keys.add(key)
            if key in open_by_key:
                continue

            exc = ExceptionRecord(
                exception_id=format_exception_id(year, next_seq),
                optimization_run_id=optimization_run_id,
                category=f["category"],
                issue_type=f["issue_type"],
                severity=f["severity"],
                entity_type=f["entity_type"],
                entity_id=f["entity_id"],
                related_entity_ids_json=f["related_ids"],
                description=f["description"],
                detected_at=now_dt,
                resolution_status=ExceptionResolutionStatus.OPEN.value,
                recommended_action=f["recommended_action"],
            )
            next_seq += 1
            session.add(exc)
            newly_created.append(exc)

        auto_resolved_count = 0
        if auto_resolve_remediated:
            remediable_types = {
                ExceptionIssueType.CAPACITY_EXCEEDED.value,
                ExceptionIssueType.INCOMPATIBLE_COLOCATION.value,
                ExceptionIssueType.DUPLICATE_ACTIVE_ASSIGNMENT.value,
                ExceptionIssueType.CAPACITY_NEARLY_EXHAUSTED.value,
                ExceptionIssueType.INVENTORY_EXCEEDS_ASSIGNED_CAPACITY.value,
            }
            for key, old_exc in open_by_key.items():
                if old_exc.issue_type in remediable_types and key not in active_finding_keys:
                    old_exc.resolution_status = ExceptionResolutionStatus.RESOLVED.value
                    old_exc.resolved_at = now_dt
                    old_exc.resolution_note = (
                        "Automatically resolved after slotting optimization remediated violation."
                    )
                    auto_resolved_count += 1

        session.flush()

        if newly_created or auto_resolved_count > 0:
            self.audit_service.log_event(
                session=session,
                event_type="EXCEPTION_SCAN_COMPLETED",
                entity_type="SYSTEM",
                entity_id=optimization_run_id or "OPERATIONAL_SCAN",
                actor=actor,
                previous_state=None,
                new_state={
                    "active_findings_total": len(findings),
                    "newly_created_count": len(newly_created),
                    "auto_resolved_count": auto_resolved_count,
                },
                timestamp=now_dt,
            )
            session.flush()

        by_type: dict[str, int] = {}
        by_severity: dict[str, int] = {}
        for f in findings:
            by_type[f["issue_type"]] = by_type.get(f["issue_type"], 0) + 1
            by_severity[f["severity"]] = by_severity.get(f["severity"], 0) + 1

        return {
            "active_findings_count": len(findings),
            "newly_created_count": len(newly_created),
            "auto_resolved_count": auto_resolved_count,
            "by_issue_type": dict(sorted(by_type.items())),
            "by_severity": dict(sorted(by_severity.items())),
        }

    def update_exception_status(
        self,
        session: Session,
        exception_id: str,
        new_status: str,
        resolution_note: str | None = None,
        actor: str = "OPERATOR",
    ) -> ExceptionRecord:
        """Transition an exception's resolution lifecycle status with strict state-machine validation."""
        exc = session.get(ExceptionRecord, exception_id)
        if exc is None:
            raise KeyError(f"ExceptionRecord not found: {exception_id}")

        curr_enum = ExceptionResolutionStatus(exc.resolution_status)
        target_enum = ExceptionResolutionStatus(new_status)
        if target_enum != curr_enum and target_enum not in VALID_EXCEPTION_TRANSITIONS[curr_enum]:
            raise ValueError(f"Invalid exception lifecycle transition: {curr_enum.value} -> {target_enum.value}")

        now_dt = utc_now_naive()
        prev_status = exc.resolution_status
        exc.resolution_status = target_enum.value
        if resolution_note:
            exc.resolution_note = resolution_note
        if target_enum in {ExceptionResolutionStatus.RESOLVED, ExceptionResolutionStatus.DISMISSED}:
            exc.resolved_at = now_dt
        elif target_enum in {ExceptionResolutionStatus.OPEN, ExceptionResolutionStatus.IN_REVIEW}:
            exc.resolved_at = None

        self.audit_service.log_event(
            session=session,
            event_type="EXCEPTION_STATUS_UPDATED",
            entity_type="EXCEPTION",
            entity_id=exception_id,
            actor=actor,
            previous_state={"resolution_status": prev_status},
            new_state={"resolution_status": target_enum.value, "resolution_note": resolution_note},
            timestamp=now_dt,
        )
        session.flush()
        return exc

    def _scan_operational_findings(self, session: Session) -> list[dict[str, Any]]:
        """Evaluate all mandatory and supplemental exception rules against live operational tables."""
        findings: list[dict[str, Any]] = []

        warehouses = {w.warehouse_id: w for w in session.scalars(select(Warehouse)).all()}
        zones = {z.zone_id: z for z in session.scalars(select(Zone)).all()}
        aisles = {a.aisle_id: a for a in session.scalars(select(Aisle)).all()}
        locations = {loc.location_id: loc for loc in session.scalars(select(Location)).all()}
        skus = {s.sku_id: s for s in session.scalars(select(SKU)).all()}
        profiles = {
            p.sku_id: p for p in session.scalars(select(SKUProfile).where(SKUProfile.effective_to.is_(None))).all()
        }
        all_assignments = session.scalars(select(SlotAssignment).order_by(SlotAssignment.slot_assignment_id)).all()

        zone_hazards: dict[str, set[str]] = {}
        for al in session.scalars(select(ZoneHazardAllowance)).all():
            zone_hazards.setdefault(al.zone_id, set()).add(al.hazard_class_id)

        rules_orm = session.scalars(select(CompatibilityRule)).all()
        conflict_rules = PairwiseConflictRules.from_rule_dicts(
            [
                {
                    "rule_type": r.rule_type,
                    "primary_attribute": r.primary_attribute,
                    "conflicting_attribute": r.conflicting_attribute,
                    "is_hard_constraint": r.is_hard_constraint,
                }
                for r in rules_orm
            ]
        )
        compat_service = CompatibilityService(conflict_rules=conflict_rules)

        sku_specs: dict[str, SKUSpec] = {
            sid: SKUSpec(
                sku_id=s.sku_id,
                category=s.category,
                unit_weight_kg=float(s.unit_weight_kg),
                width_cm=float(s.width_cm),
                height_cm=float(s.height_cm),
                depth_cm=float(s.depth_cm),
                is_fragile=bool(s.is_fragile),
                hazard_class=s.hazard_class,
                temperature_requirement=s.temperature_requirement,
                storage_type=s.storage_type,
                status=s.status,
            )
            for sid, s in skus.items()
        }

        active_by_loc: dict[str, list[SlotAssignment]] = {}
        active_by_sku: dict[str, list[SlotAssignment]] = {}
        for assn in all_assignments:
            if assn.assignment_status == AssignmentStatus.ACTIVE.value:
                active_by_loc.setdefault(assn.location_id, []).append(assn)
                active_by_sku.setdefault(assn.sku_id, []).append(assn)
                if assn.effective_to is not None:
                    findings.append(
                        {
                            "category": ExceptionCategory.DATA_QUALITY_VIOLATION.value,
                            "issue_type": ExceptionIssueType.INVALID_LIFECYCLE_TRANSITION.value,
                            "severity": ExceptionSeverity.HIGH.value,
                            "entity_type": "SLOT_ASSIGNMENT",
                            "entity_id": assn.slot_assignment_id,
                            "related_ids": [assn.sku_id, assn.location_id],
                            "description": f"Assignment {assn.slot_assignment_id} is ACTIVE with non-null effective_to.",
                            "recommended_action": "Clear effective_to or transition record status to SUPERSEDED.",
                        }
                    )

        # 1 & 7. CAPACITY_EXCEEDED and CAPACITY_NEARLY_EXHAUSTED
        near_thr = self.config.exceptions.capacity_near_exhaustion_ratio
        for lid, loc in sorted(locations.items()):
            loc_assns = active_by_loc.get(lid, [])
            assn_wt = sum(float(a.allocated_weight_kg) for a in loc_assns)
            assn_vol = sum(float(a.allocated_volume_m3) for a in loc_assns)
            eff_wt = max(float(loc.current_weight_kg), assn_wt)
            eff_vol = max(float(loc.current_volume_m3), assn_vol)
            rel_ids = [a.slot_assignment_id for a in loc_assns] + [a.sku_id for a in loc_assns]

            if eff_wt > float(loc.max_weight_kg) + 1e-5 or eff_vol > float(loc.max_volume_m3) + 1e-6:
                findings.append(
                    {
                        "category": ExceptionCategory.BUSINESS_RULE_VIOLATION.value,
                        "issue_type": ExceptionIssueType.CAPACITY_EXCEEDED.value,
                        "severity": ExceptionSeverity.CRITICAL.value,
                        "entity_type": "LOCATION",
                        "entity_id": lid,
                        "related_ids": rel_ids,
                        "description": (
                            f"Location {lid} exceeds physical capacity: weight {eff_wt:.2f}/{loc.max_weight_kg:.2f} kg, "
                            f"volume {eff_vol:.4f}/{loc.max_volume_m3:.4f} m3."
                        ),
                        "recommended_action": "Re-slot excess inventory to a higher-capacity bay or split lot to bulk reserve.",
                    }
                )
            else:
                wt_ratio = eff_wt / max(float(loc.max_weight_kg), 1e-6)
                vol_ratio = eff_vol / max(float(loc.max_volume_m3), 1e-9)
                peak_ratio = max(wt_ratio, vol_ratio)
                if peak_ratio >= near_thr:
                    findings.append(
                        {
                            "category": ExceptionCategory.OPERATIONAL_WARNING.value,
                            "issue_type": ExceptionIssueType.CAPACITY_NEARLY_EXHAUSTED.value,
                            "severity": ExceptionSeverity.MEDIUM.value,
                            "entity_type": "LOCATION",
                            "entity_id": lid,
                            "related_ids": rel_ids,
                            "description": (
                                f"Location {lid} at {peak_ratio * 100:.1f}% capacity utilization "
                                f"(threshold {near_thr * 100:.0f}%)."
                            ),
                            "recommended_action": "Monitor forward-pick bin headroom or adjust replenishment lot size.",
                        }
                    )

        # 2 & 3. DUPLICATE_ACTIVE_ASSIGNMENT and INCOMPATIBLE_COLOCATION
        for lid, loc_assns in sorted(active_by_loc.items()):
            loc = locations[lid]
            zone = zones[loc.zone_id]
            allowed_hz = zone_hazards.get(zone.zone_id, set())

            if len(loc_assns) > 1 and loc.storage_policy == StoragePolicy.DEDICATED_SINGLE_SKU.value:
                findings.append(
                    {
                        "category": ExceptionCategory.BUSINESS_RULE_VIOLATION.value,
                        "issue_type": ExceptionIssueType.DUPLICATE_ACTIVE_ASSIGNMENT.value,
                        "severity": ExceptionSeverity.HIGH.value,
                        "entity_type": "LOCATION",
                        "entity_id": lid,
                        "related_ids": [a.slot_assignment_id for a in loc_assns] + [a.sku_id for a in loc_assns],
                        "description": (
                            f"Dedicated single-SKU location {lid} holds {len(loc_assns)} active assignments: "
                            f"{[a.sku_id for a in loc_assns]}."
                        ),
                        "recommended_action": "Re-assign secondary SKU(s) to open dedicated slots and supersede duplicate assignment.",
                    }
                )

            # Check pairwise SKU co-location compatibility + zone hazard/thermal mismatch
            incomp_reasons: list[str] = []
            for a in loc_assns:
                sp = sku_specs[a.sku_id]
                if sp.temperature_requirement != zone.temperature_type:
                    incomp_reasons.append(
                        f"TEMP_MISMATCH({sp.sku_id}:{sp.temperature_requirement}!={zone.temperature_type})"
                    )
                if sp.hazard_class not in allowed_hz:
                    incomp_reasons.append(f"HAZARD_MISMATCH({sp.sku_id}:{sp.hazard_class})")

            if len(loc_assns) >= 2:
                for i in range(len(loc_assns)):
                    for j in range(i + 1, len(loc_assns)):
                        sa = sku_specs[loc_assns[i].sku_id]
                        sb = sku_specs[loc_assns[j].sku_id]
                        ok, r_list = compat_service.are_two_skus_compatible(sa, sb)
                        if not ok:
                            incomp_reasons.extend(r_list)

            if incomp_reasons:
                findings.append(
                    {
                        "category": ExceptionCategory.BUSINESS_RULE_VIOLATION.value,
                        "issue_type": ExceptionIssueType.INCOMPATIBLE_COLOCATION.value,
                        "severity": ExceptionSeverity.CRITICAL.value,
                        "entity_type": "LOCATION",
                        "entity_id": lid,
                        "related_ids": [a.slot_assignment_id for a in loc_assns] + [a.sku_id for a in loc_assns],
                        "description": (
                            f"Incompatible product storage in {lid} ({[a.sku_id for a in loc_assns]}): "
                            f"{', '.join(sorted(set(incomp_reasons)))}."
                        ),
                        "recommended_action": "Immediately segregate conflicting hazardous or food/chemical SKUs into compliant zones.",
                    }
                )

        # 4. BLOCKED_AISLE_PICK_PATH
        picks = session.scalars(select(PickEvent).order_by(PickEvent.pick_event_id)).all()
        for pe in picks:
            loc = locations.get(pe.location_id)
            if loc is None:
                continue
            aisle = aisles.get(loc.aisle_id)
            if bool(loc.is_blocked) or (aisle is not None and bool(aisle.is_blocked)):
                aid = aisle.aisle_id if aisle else "UNKNOWN_AISLE"
                findings.append(
                    {
                        "category": ExceptionCategory.BUSINESS_RULE_VIOLATION.value,
                        "issue_type": ExceptionIssueType.BLOCKED_AISLE_PICK_PATH.value,
                        "severity": ExceptionSeverity.HIGH.value,
                        "entity_type": "PICK_EVENT",
                        "entity_id": pe.pick_event_id,
                        "related_ids": [aid, pe.location_id, pe.order_id, pe.sku_id],
                        "description": (
                            f"Pick event {pe.pick_event_id} (Order {pe.order_id}) routed through blocked aisle {aid} "
                            f"at location {pe.location_id}."
                        ),
                        "recommended_action": "Reroute pick tour via adjacent unblocked cross-aisle and relocate active forward pick stock.",
                    }
                )

        # 5. REPLENISHMENT_LABOR_OVERLOAD
        active_reps = session.scalars(
            select(Replenishment)
            .where(
                Replenishment.replenishment_status.in_(
                    [
                        ReplenishmentStatus.PENDING.value,
                        ReplenishmentStatus.IN_PROGRESS.value,
                        ReplenishmentStatus.OVERLOADED.value,
                    ]
                )
            )
            .order_by(Replenishment.replenishment_id)
        ).all()

        reps_by_wh: dict[str, list[Replenishment]] = {}
        for rep in active_reps:
            reps_by_wh.setdefault(rep.warehouse_id, []).append(rep)

        for wid, r_list in sorted(reps_by_wh.items()):
            wh = warehouses.get(wid)
            if wh is None:
                continue
            total_req_min = sum(float(r.labor_minutes_required) for r in r_list)
            cap_min = float(wh.daily_labor_minutes_capacity)
            explicit_overloads = [
                r.replenishment_id
                for r in r_list
                if r.replenishment_status == ReplenishmentStatus.OVERLOADED.value
            ]
            if total_req_min > cap_min + 1e-3 or explicit_overloads:
                overload_ids = explicit_overloads or [r.replenishment_id for r in r_list[:5]]
                findings.append(
                    {
                        "category": ExceptionCategory.OPERATIONAL_WARNING.value,
                        "issue_type": ExceptionIssueType.REPLENISHMENT_LABOR_OVERLOAD.value,
                        "severity": ExceptionSeverity.HIGH.value,
                        "entity_type": "WAREHOUSE",
                        "entity_id": wid,
                        "related_ids": overload_ids,
                        "description": (
                            f"Warehouse {wid} replenishment labor overload: {total_req_min:.1f} min required vs "
                            f"{cap_min:.1f} min nominal daily capacity ({len(explicit_overloads)} OVERLOADED tasks)."
                        ),
                        "recommended_action": "Reallocate cross-zone labor, prioritize CRITICAL fast-mover replenishments, or increase forward-pick slot sizes.",
                    }
                )

        # 6 & 8 & 11. NO_COMPATIBLE_LOCATION, MISSING_SKU_PROFILE, EXCESSIVE_REPLENISHMENT_FREQUENCY
        active_unblocked_locs = [
            loc
            for loc in locations.values()
            if loc.status == EntityStatus.ACTIVE.value
            and not loc.is_blocked
            and not aisles[loc.aisle_id].is_blocked
            and zones[loc.zone_id].status == EntityStatus.ACTIVE.value
            and warehouses[zones[loc.zone_id].warehouse_id].status == EntityStatus.ACTIVE.value
        ]
        freq_thr = self.config.exceptions.excessive_replenishment_monthly_threshold

        for sid, sku in sorted(skus.items()):
            if sku.status != SKUStatus.ACTIVE.value:
                continue
            if sid not in profiles:
                findings.append(
                    {
                        "category": ExceptionCategory.DATA_QUALITY_VIOLATION.value,
                        "issue_type": ExceptionIssueType.MISSING_SKU_PROFILE.value,
                        "severity": ExceptionSeverity.HIGH.value,
                        "entity_type": "SKU",
                        "entity_id": sid,
                        "related_ids": [],
                        "description": f"Active SKU {sid} has no active analytical SKUProfile.",
                        "recommended_action": "Run DemandAnalyticsService profile derivation for this SKU.",
                    }
                )
            else:
                prof = profiles[sid]
                if float(prof.replenishment_frequency) > freq_thr:
                    findings.append(
                        {
                            "category": ExceptionCategory.OPERATIONAL_WARNING.value,
                            "issue_type": ExceptionIssueType.EXCESSIVE_REPLENISHMENT_FREQUENCY.value,
                            "severity": ExceptionSeverity.LOW.value,
                            "entity_type": "SKU",
                            "entity_id": sid,
                            "related_ids": [prof.profile_id],
                            "description": (
                                f"SKU {sid} projected replenishment frequency ({prof.replenishment_frequency:.1f}/mo) "
                                f"exceeds threshold ({freq_thr:.1f}/mo)."
                            ),
                            "recommended_action": "Allocate larger forward-pick location to increase days-of-supply per replenishment.",
                        }
                    )

            # Check physical/environmental compatibility existence across active facility locations
            if sid not in active_by_sku:
                sp = sku_specs[sid]
                has_any_fit = False
                for loc in active_unblocked_locs:
                    z = zones[loc.zone_id]
                    if sp.temperature_requirement != z.temperature_type:
                        continue
                    if sp.hazard_class not in zone_hazards.get(z.zone_id, set()):
                        continue
                    if (
                        sp.width_cm <= float(loc.width_cm) + 1e-6
                        and sp.height_cm <= float(loc.height_cm) + 1e-6
                        and sp.depth_cm <= float(loc.depth_cm) + 1e-6
                        and sp.unit_weight_kg <= float(loc.max_weight_kg) + 1e-6
                        and sp.unit_volume_m3 <= float(loc.max_volume_m3) + 1e-8
                    ):
                        has_any_fit = True
                        break
                if not has_any_fit:
                    findings.append(
                        {
                            "category": ExceptionCategory.OPTIMIZATION_FAILURE.value,
                            "issue_type": ExceptionIssueType.NO_COMPATIBLE_LOCATION.value,
                            "severity": ExceptionSeverity.HIGH.value,
                            "entity_type": "SKU",
                            "entity_id": sid,
                            "related_ids": [],
                            "description": (
                                f"SKU {sid} ({sku.width_cm}x{sku.height_cm}x{sku.depth_cm} cm, {sku.unit_weight_kg} kg, "
                                f"{sku.temperature_requirement}, {sku.hazard_class}) fits 0 warehouse locations."
                            ),
                            "recommended_action": "Provision oversized bulk floor bay or assign to specialized external storage.",
                        }
                    )

        return findings
