"""Candidate warehouse-slot generation and explainable rejection-reason tracking (Section 8.5)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import DomainConfig, load_domain_config
from backend.app.models import (
    SKU,
    Aisle,
    CompatibilityRule,
    Location,
    SKUProfile,
    SlotAssignment,
    Warehouse,
    Zone,
    ZoneHazardAllowance,
)
from backend.app.models.enums import AssignmentStatus, SKUStatus
from backend.app.optimization.scoring import MultiFactorScorer, ScoreBreakdown, SKUAnalyticalMetrics
from backend.app.services.compatibility import (
    CompatibilityResult,
    CompatibilityService,
    LocationSpec,
    PairwiseConflictRules,
    SKUSpec,
)


@dataclass
class EvaluatedCandidate:
    """Single evaluated (SKU, Location) candidate preserving scores or rejection reasons."""

    sku_id: str
    location_id: str
    warehouse_id: str
    zone_id: str
    zone_type: str
    accessibility_type: str
    is_feasible: bool
    assigned_quantity: int
    allocated_weight_kg: float
    allocated_volume_m3: float
    total_score: float
    score_breakdown: ScoreBreakdown
    hard_constraints_checked: dict[str, bool]
    rejection_reasons: list[str]
    rank_position: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sku_id": self.sku_id,
            "location_id": self.location_id,
            "warehouse_id": self.warehouse_id,
            "zone_id": self.zone_id,
            "zone_type": self.zone_type,
            "accessibility_type": self.accessibility_type,
            "is_feasible": self.is_feasible,
            "assigned_quantity": self.assigned_quantity,
            "allocated_weight_kg": self.allocated_weight_kg,
            "allocated_volume_m3": self.allocated_volume_m3,
            "total_score": self.total_score,
            "rank_position": self.rank_position,
            "score_breakdown": self.score_breakdown.to_dict(),
            "hard_constraints_checked": self.hard_constraints_checked,
            "rejection_reasons": self.rejection_reasons,
        }


@dataclass
class SKUCandidatePool:
    """Complete evaluation outcome for one SKU across eligible and rejected locations."""

    sku: SKUSpec
    metrics: SKUAnalyticalMetrics
    top_feasible: list[EvaluatedCandidate]
    rejected_samples: list[EvaluatedCandidate]
    total_locations_evaluated: int
    total_feasible_count: int
    total_rejected_count: int
    rejection_reason_counts: dict[str, int]


class CandidateGeneratorService:
    """Generates, validates, scores, and ranks candidate locations while preserving rejection reasons."""

    def __init__(
        self,
        config: DomainConfig | None = None,
        storage_policy_override: str | None = None,
    ) -> None:
        self.config = config or load_domain_config()
        self.storage_policy_override = storage_policy_override
        self.scorer = MultiFactorScorer(self.config.scoring)

    @staticmethod
    def compute_candidate_quantity(
        sku: SKUSpec,
        metrics: SKUAnalyticalMetrics,
        location: LocationSpec,
        explicit_quantity: int | None = None,
        max_fill_ratio: float = 0.85,
    ) -> int:
        """Determine realistic forward-pick quantity respecting slot headroom while allowing >=1 unit for bulky SKUs."""
        if explicit_quantity is not None:
            return max(1, int(explicit_quantity))

        desired = max(1, int(metrics.desired_quantity))

        avail_wt = max(0.0, location.max_weight_kg * max_fill_ratio - location.occupied_weight_kg)
        avail_vol = max(0.0, location.max_volume_m3 * max_fill_ratio - location.occupied_volume_m3)

        max_by_wt = int(math.floor(avail_wt / max(sku.unit_weight_kg, 1e-6)))
        max_by_vol = int(math.floor(avail_vol / max(sku.unit_volume_m3, 1e-9)))
        safe_fit = min(max_by_wt, max_by_vol)

        if safe_fit >= 1:
            return min(desired, safe_fit)

        # Check if a single unit fits below the 90% near-exhaustion safety margin
        headroom_wt = max(0.0, location.max_weight_kg * 0.90 - location.occupied_weight_kg)
        headroom_vol = max(0.0, location.max_volume_m3 * 0.90 - location.occupied_volume_m3)
        if sku.unit_weight_kg <= headroom_wt and sku.unit_volume_m3 <= headroom_vol:
            return 1

        # Return 1 (or desired if unit fits >90% only) so CompatibilityService explicitly tests physical 100% bounds
        return 1

    def load_operational_context(
        self,
        session: Session,
        warehouse_id: str | None = None,
        zone_id: str | None = None,
        sku_ids: list[str] | None = None,
        vacate_in_scope_skus: bool = True,
        ml_forecasts: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[
        list[SKUSpec],
        dict[str, SKUAnalyticalMetrics],
        list[LocationSpec],
        dict[str, str],
        CompatibilityService,
        dict[str, float],
    ]:
        """Load SKUs, profiles, locations, active occupancy, and compatibility rules from DB."""
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
        compat_service = CompatibilityService(
            conflict_rules=conflict_rules,
            storage_policy_override=self.storage_policy_override,
        )

        # Load all SKUs into lookup map (needed for co-located out-of-scope SKUs too)
        all_skus_orm = session.scalars(select(SKU).order_by(SKU.sku_id)).all()
        all_sku_specs: dict[str, SKUSpec] = {
            s.sku_id: SKUSpec(
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
            for s in all_skus_orm
        }

        # Load active profiles
        profiles_orm = session.scalars(select(SKUProfile).where(SKUProfile.effective_to.is_(None))).all()
        max_pick_freq = max((float(p.pick_frequency) for p in profiles_orm), default=1.0)
        if max_pick_freq <= 0.0:
            max_pick_freq = 1.0

        metrics_map: dict[str, SKUAnalyticalMetrics] = {}
        for p in profiles_orm:
            f_item = ml_forecasts.get(p.sku_id) if ml_forecasts else None
            eff_adv = (
                float(f_item["forecast_daily_demand"]) if f_item is not None else float(p.average_daily_demand)
            )
            eff_vel = str(f_item["forecast_velocity"]) if f_item is not None else p.demand_velocity
            eff_pct = (
                float(f_item["forecast_velocity_percentile"])
                if f_item is not None
                else float(p.velocity_percentile)
            )
            # Scale pick frequency proportionally when ML forecast predicts demand surge/decline
            hist_adv = float(p.average_daily_demand)
            if f_item is not None and hist_adv > 0.05:
                eff_pick_freq = float(p.pick_frequency) * min(2.5, max(0.25, eff_adv / hist_adv))
            else:
                eff_pick_freq = float(p.pick_frequency)

            desired_qty = max(
                1,
                min(
                    int(p.current_on_hand_qty) if p.current_on_hand_qty > 0 else 1,
                    max(1, int(math.ceil(eff_adv * int(p.target_days_of_supply)))),
                )
                if eff_adv > 0
                else max(1, int(p.current_on_hand_qty)),
            )
            metrics_map[p.sku_id] = SKUAnalyticalMetrics(
                sku_id=p.sku_id,
                average_daily_demand=eff_adv,
                demand_velocity=eff_vel,
                velocity_percentile=eff_pct,
                pick_frequency=eff_pick_freq,
                normalized_pick_frequency=min(1.0, eff_pick_freq / max_pick_freq),
                abc_class=p.abc_class,
                xyz_class=p.xyz_class,
                replenishment_frequency=float(p.replenishment_frequency),
                target_days_of_supply=int(p.target_days_of_supply),
                desired_quantity=desired_qty,
            )

        # Load active assignments to establish current warehouse affinity & locked occupancy
        active_assns = session.scalars(
            select(SlotAssignment)
            .where(SlotAssignment.assignment_status == AssignmentStatus.ACTIVE.value)
            .order_by(SlotAssignment.slot_assignment_id)
        ).all()

        sku_current_wh: dict[str, str] = {}
        sku_current_loc: dict[str, str] = {}
        for a in active_assns:
            sku_current_wh.setdefault(a.sku_id, a.warehouse_id)
            sku_current_loc.setdefault(a.sku_id, a.location_id)

        # Load warehouses, zones, hazard allowances, aisles, and locations
        wh_stmt = select(Warehouse).order_by(Warehouse.warehouse_id)
        if warehouse_id:
            wh_stmt = wh_stmt.where(Warehouse.warehouse_id == warehouse_id)
        warehouses_map = {w.warehouse_id: w for w in session.scalars(wh_stmt).all()}

        zone_stmt = select(Zone).order_by(Zone.zone_id)
        if warehouse_id:
            zone_stmt = zone_stmt.where(Zone.warehouse_id == warehouse_id)
        if zone_id:
            zone_stmt = zone_stmt.where(Zone.zone_id == zone_id)
        zones_map = {z.zone_id: z for z in session.scalars(zone_stmt).all()}

        allowances_orm = session.scalars(select(ZoneHazardAllowance)).all()
        zone_hazards: dict[str, set[str]] = {}
        for al in allowances_orm:
            zone_hazards.setdefault(al.zone_id, set()).add(al.hazard_class_id)

        aisles_map = {a.aisle_id: a for a in session.scalars(select(Aisle)).all()}

        # Determine SKUs in scope
        target_sku_specs: list[SKUSpec] = []
        requested_set = set(sku_ids) if sku_ids else None
        for sid, s_spec in all_sku_specs.items():
            if s_spec.status != SKUStatus.ACTIVE.value or sid not in metrics_map:
                continue
            if requested_set is not None and sid not in requested_set:
                continue
            if requested_set is None:
                if zone_id:
                    curr_loc_id = sku_current_loc.get(sid)
                    curr_loc_obj = session.get(Location, curr_loc_id) if curr_loc_id else None
                    if curr_loc_obj is None or curr_loc_obj.zone_id != zone_id:
                        continue
                elif warehouse_id:
                    curr_wh = sku_current_wh.get(sid)
                    # Include SKUs currently in warehouse_id plus any unassigned SKUs
                    if curr_wh is not None and curr_wh != warehouse_id:
                        continue
            target_sku_specs.append(s_spec)

        in_scope_sku_ids = {s.sku_id for s in target_sku_specs}

        # Compute residual locked occupancy on locations (excluding in-scope SKUs if vacating for re-slotting)
        locked_wt: dict[str, float] = {}
        locked_vol: dict[str, float] = {}
        locked_skus: dict[str, list[SKUSpec]] = {}
        for a in active_assns:
            if vacate_in_scope_skus and a.sku_id in in_scope_sku_ids:
                continue
            lid = a.location_id
            locked_wt[lid] = locked_wt.get(lid, 0.0) + float(a.allocated_weight_kg)
            locked_vol[lid] = locked_vol.get(lid, 0.0) + float(a.allocated_volume_m3)
            if a.sku_id in all_sku_specs:
                locked_skus.setdefault(lid, []).append(all_sku_specs[a.sku_id])

        loc_stmt = (
            select(Location).where(Location.zone_id.in_(list(zones_map.keys()))).order_by(Location.location_id)
        )
        locs_orm = session.scalars(loc_stmt).all() if zones_map else []

        location_specs: list[LocationSpec] = []
        max_wh_dist: dict[str, float] = {}
        for loc in locs_orm:
            z = zones_map[loc.zone_id]
            w = warehouses_map.get(z.warehouse_id)
            if w is None:
                continue
            aisle = aisles_map[loc.aisle_id]
            occ_wt = round(locked_wt.get(loc.location_id, 0.0), 4)
            occ_vol = round(locked_vol.get(loc.location_id, 0.0), 6)
            coloc = tuple(locked_skus.get(loc.location_id, []))
            dist = abs(float(loc.x_coordinate) - float(w.depot_x)) + abs(
                float(loc.y_coordinate) - float(w.depot_y)
            )
            if dist > max_wh_dist.get(w.warehouse_id, 20.0):
                max_wh_dist[w.warehouse_id] = dist

            location_specs.append(
                LocationSpec(
                    location_id=loc.location_id,
                    warehouse_id=w.warehouse_id,
                    warehouse_status=w.status,
                    zone_id=z.zone_id,
                    zone_type=z.zone_type,
                    zone_status=z.status,
                    temperature_type=z.temperature_type,
                    allowed_hazard_classes=frozenset(zone_hazards.get(z.zone_id, set())),
                    aisle_id=aisle.aisle_id,
                    aisle_is_blocked=bool(aisle.is_blocked),
                    x_coordinate=float(loc.x_coordinate),
                    y_coordinate=float(loc.y_coordinate),
                    depot_x=float(w.depot_x),
                    depot_y=float(w.depot_y),
                    width_cm=float(loc.width_cm),
                    height_cm=float(loc.height_cm),
                    depth_cm=float(loc.depth_cm),
                    max_weight_kg=float(loc.max_weight_kg),
                    max_volume_m3=float(loc.max_volume_m3),
                    occupied_weight_kg=occ_wt,
                    occupied_volume_m3=occ_vol,
                    accessibility_type=loc.accessibility_type,
                    storage_policy=loc.storage_policy,
                    is_blocked=bool(loc.is_blocked),
                    status=loc.status,
                    co_located_skus=coloc,
                )
            )

        return (
            target_sku_specs,
            metrics_map,
            location_specs,
            sku_current_wh,
            compat_service,
            max_wh_dist,
        )

    def evaluate_candidates_for_sku(
        self,
        sku: SKUSpec,
        metrics: SKUAnalyticalMetrics,
        locations: list[LocationSpec],
        compat_service: CompatibilityService,
        max_wh_dist: dict[str, float],
        top_n: int | None = None,
        max_rejected_samples: int = 10,
        explicit_quantity: int | None = None,
    ) -> SKUCandidatePool:
        """Evaluate candidate slots for a single SKU, ranking feasible slots and preserving rejections."""
        limit_n = top_n if top_n is not None else self.config.optimization.top_n_candidates
        feasible: list[EvaluatedCandidate] = []
        rejected_samples: list[EvaluatedCandidate] = []
        reason_counts: dict[str, int] = {}
        rejected_total = 0

        for loc in locations:
            qty = self.compute_candidate_quantity(
                sku=sku,
                metrics=metrics,
                location=loc,
                explicit_quantity=explicit_quantity,
            )
            compat: CompatibilityResult = compat_service.evaluate(sku=sku, location=loc, quantity=qty)
            max_d = max_wh_dist.get(loc.warehouse_id, 80.0)
            breakdown = self.scorer.score_candidate(
                sku=sku,
                metrics=metrics,
                location=loc,
                compatibility=compat,
                max_warehouse_distance_m=max_d,
            )

            cand = EvaluatedCandidate(
                sku_id=sku.sku_id,
                location_id=loc.location_id,
                warehouse_id=loc.warehouse_id,
                zone_id=loc.zone_id,
                zone_type=loc.zone_type,
                accessibility_type=loc.accessibility_type,
                is_feasible=compat.is_feasible,
                assigned_quantity=qty,
                allocated_weight_kg=compat.required_weight_kg,
                allocated_volume_m3=compat.required_volume_m3,
                total_score=breakdown.total_score,
                score_breakdown=breakdown,
                hard_constraints_checked=compat.hard_constraints_checked,
                rejection_reasons=compat.rejection_reasons,
            )

            if compat.is_feasible:
                feasible.append(cand)
            else:
                rejected_total += 1
                for code in compat.rejection_reasons:
                    reason_counts[code] = reason_counts.get(code, 0) + 1
                if len(rejected_samples) < max_rejected_samples:
                    rejected_samples.append(cand)

        # Deterministic ranking: highest score, shortest dock distance, lexicographic location_id
        feasible.sort(key=lambda c: (-c.total_score, c.score_breakdown.dock_distance_m, c.location_id))
        for rank_idx, fc in enumerate(feasible[:limit_n], start=1):
            fc.rank_position = rank_idx

        return SKUCandidatePool(
            sku=sku,
            metrics=metrics,
            top_feasible=feasible[:limit_n],
            rejected_samples=rejected_samples,
            total_locations_evaluated=len(locations),
            total_feasible_count=len(feasible),
            total_rejected_count=rejected_total,
            rejection_reason_counts=dict(sorted(reason_counts.items())),
        )
