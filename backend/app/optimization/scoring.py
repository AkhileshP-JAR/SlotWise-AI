"""Configurable, explainable 6-factor SKU-Location scoring engine (Section 8.4).

All component scores are strictly normalized to [0.0, 1.0].
Infeasible candidates ALWAYS receive total_score = 0.0; scoring is purely a ranking
mechanism over hard-constraint-feasible candidates.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.config import ScoringConfig
from backend.app.models.enums import (
    AccessibilityType,
    DemandVelocityClass,
    HazardCode,
    StorageType,
    ZoneType,
)
from backend.app.services.compatibility import CompatibilityResult, LocationSpec, SKUSpec


@dataclass(frozen=True)
class SKUAnalyticalMetrics:
    """Analytical demand and replenishment attributes used during multi-factor scoring."""

    sku_id: str
    average_daily_demand: float
    demand_velocity: str
    velocity_percentile: float
    pick_frequency: float
    normalized_pick_frequency: float
    abc_class: str
    xyz_class: str
    replenishment_frequency: float
    target_days_of_supply: int
    desired_quantity: int


@dataclass
class ScoreBreakdown:
    """Explainable normalized component scores and weighted total score in [0.0, 1.0]."""

    total_score: float
    velocity_score: float
    pick_frequency_score: float
    distance_score: float
    capacity_score: float
    replenishment_score: float
    compatibility_score: float
    dock_distance_m: float
    explanation: str

    def to_dict(self) -> dict[str, float | str]:
        return {
            "total_score": self.total_score,
            "velocity_score": self.velocity_score,
            "pick_frequency_score": self.pick_frequency_score,
            "distance_score": self.distance_score,
            "capacity_score": self.capacity_score,
            "replenishment_score": self.replenishment_score,
            "compatibility_score": self.compatibility_score,
            "dock_distance_m": self.dock_distance_m,
            "explanation": self.explanation,
        }


class MultiFactorScorer:
    """Computes explainable 6-component weighted slotting scores for feasible candidate pairs."""

    def __init__(self, config: ScoringConfig | None = None) -> None:
        self.config = config or ScoringConfig()

    def score_candidate(
        self,
        sku: SKUSpec,
        metrics: SKUAnalyticalMetrics,
        location: LocationSpec,
        compatibility: CompatibilityResult,
        max_warehouse_distance_m: float,
    ) -> ScoreBreakdown:
        """Compute normalized [0, 1] component scores. Returns 0.0 across all scores if infeasible."""
        dock_dist = abs(location.x_coordinate - location.depot_x) + abs(location.y_coordinate - location.depot_y)

        if not compatibility.is_feasible:
            return ScoreBreakdown(
                total_score=0.0,
                velocity_score=0.0,
                pick_frequency_score=0.0,
                distance_score=0.0,
                capacity_score=0.0,
                replenishment_score=0.0,
                compatibility_score=0.0,
                dock_distance_m=round(dock_dist, 3),
                explanation=f"Rejected due to failed hard constraints: {', '.join(compatibility.rejection_reasons)}",
            )

        # 1. Demand Velocity Score (ergonomic accessibility matched to physical movement velocity)
        is_pallet_or_heavy = (
            sku.storage_type
            in {
                StorageType.PALLET.value,
                StorageType.OVERSIZE_FLOOR.value,
            }
            or sku.unit_weight_kg >= 18.0
        )

        if is_pallet_or_heavy:
            acc_map = {
                AccessibilityType.FLOOR_PALLET.value: 1.0,
                AccessibilityType.GOLDEN_ZONE.value: 0.85,
                AccessibilityType.STANDARD_REACH.value: 0.65,
                AccessibilityType.HIGH_RACK_FORKLIFT.value: 0.35,
            }
        else:
            acc_map = {
                AccessibilityType.GOLDEN_ZONE.value: 1.0,
                AccessibilityType.STANDARD_REACH.value: 0.80,
                AccessibilityType.FLOOR_PALLET.value: 0.58,
                AccessibilityType.HIGH_RACK_FORKLIFT.value: 0.28,
            }
        acc_tier = acc_map.get(location.accessibility_type, 0.5)
        vel_pct = min(1.0, max(0.0, float(metrics.velocity_percentile)))
        velocity_score = max(0.0, min(1.0, 1.0 - abs(vel_pct - acc_tier)))

        # 2. Pick Frequency Score (alignment between normalized pick frequency and dock proximity)
        denom = max(10.0, float(max_warehouse_distance_m))
        dock_proximity = max(0.0, min(1.0, 1.0 - (dock_dist / denom)))
        norm_freq = min(1.0, max(0.0, float(metrics.normalized_pick_frequency)))
        pick_frequency_score = max(0.0, min(1.0, 1.0 - abs(norm_freq - dock_proximity)))

        # 3. Distance Score (high-pick SKUs strongly rewarded for near-dock placement; slow tail preserves frontage)
        pick_intensity = 0.25 + 0.75 * norm_freq
        if metrics.demand_velocity == DemandVelocityClass.FAST.value:
            distance_score = dock_proximity
        elif metrics.demand_velocity == DemandVelocityClass.MEDIUM.value:
            distance_score = pick_intensity * dock_proximity + (1.0 - pick_intensity) * (
                1.0 - 0.5 * dock_proximity
            )
        else:
            # Slow movers prefer mid-warehouse so prime dock locations stay open for fast movers
            distance_score = max(0.0, min(1.0, 1.0 - abs(0.35 - dock_proximity)))
        distance_score = max(0.0, min(1.0, distance_score))

        # 4. Capacity Utilization Score (peaks at ideal_capacity_utilization = 0.78)
        ideal = self.config.ideal_capacity_utilization
        dom_util = max(compatibility.post_weight_utilization, compatibility.post_volume_utilization)
        spread = max(ideal, 1.0 - ideal)
        capacity_score = max(0.05, min(1.0, 1.0 - abs(dom_util - ideal) / spread))
        if dom_util >= 0.92:
            # Soft penalty for pushing slot into near-exhaustion warning band
            capacity_score *= 0.65

        # 5. Replenishment Efficiency Score (rewards accessible reserve-replenishable tiers & full DOS fit)
        dos_fit = min(1.0, compatibility.quantity / max(1, metrics.desired_quantity))
        rep_ergonomics = (
            1.0
            if location.accessibility_type
            in {AccessibilityType.FLOOR_PALLET.value, AccessibilityType.GOLDEN_ZONE.value}
            else (0.82 if location.accessibility_type == AccessibilityType.STANDARD_REACH.value else 0.50)
        )
        replenishment_score = max(0.0, min(1.0, 0.55 * dos_fit + 0.45 * rep_ergonomics))

        # 6. Zone Compatibility Score (functional zone affinity)
        if sku.hazard_class not in {HazardCode.NONE.value, HazardCode.LITHIUM_BATTERY.value}:
            compatibility_score = 1.0 if location.zone_type == ZoneType.HAZMAT.value else 0.5
        elif (
            location.zone_type == ZoneType.COLD_CHAIN.value
            or is_pallet_or_heavy
            and location.zone_type == ZoneType.OVERSIZE.value
            or (
                metrics.demand_velocity == DemandVelocityClass.FAST.value
                and location.zone_type == ZoneType.FAST_PICK.value
            )
        ):
            compatibility_score = 1.0
        elif (
            metrics.demand_velocity != DemandVelocityClass.FAST.value
            and location.zone_type == ZoneType.GENERAL.value
        ):
            compatibility_score = 0.96
        else:
            compatibility_score = 0.72

        w = self.config.weights
        total = (
            w.demand_velocity * velocity_score
            + w.pick_frequency * pick_frequency_score
            + w.distance * distance_score
            + w.capacity_utilization * capacity_score
            + w.replenishment_efficiency * replenishment_score
            + w.zone_compatibility * compatibility_score
        )

        v_s = round(velocity_score, 4)
        f_s = round(pick_frequency_score, 4)
        d_s = round(distance_score, 4)
        c_s = round(capacity_score, 4)
        r_s = round(replenishment_score, 4)
        z_s = round(compatibility_score, 4)
        t_s = round(max(0.0, min(1.0, total)), 4)

        explanation = (
            f"Selected/Evaluated {location.location_id} ({location.zone_type}, {location.accessibility_type}, "
            f"{dock_dist:.1f}m from dock) for {metrics.demand_velocity} mover {sku.sku_id} "
            f"(ABC={metrics.abc_class}, qty={compatibility.quantity}): total={t_s:.4f} "
            f"[vel={v_s:.2f}, freq={f_s:.2f}, dist={d_s:.2f}, cap={c_s:.2f}, rep={r_s:.2f}, zone={z_s:.2f}]."
        )

        return ScoreBreakdown(
            total_score=t_s,
            velocity_score=v_s,
            pick_frequency_score=f_s,
            distance_score=d_s,
            capacity_score=c_s,
            replenishment_score=r_s,
            compatibility_score=z_s,
            dock_distance_m=round(dock_dist, 3),
            explanation=explanation,
        )
