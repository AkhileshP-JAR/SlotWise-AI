"""Physical, dimensional, weight, volume, thermal, hazard, and co-location compatibility engine.

Implements Sections 8.2 and 8.3 as pure, deterministic, independently testable rules.
Hard constraints evaluated here can NEVER be overridden by soft scoring weights.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.app.models.enums import (
    AccessibilityType,
    EntityStatus,
    HazardCode,
    SKUCategory,
    StoragePolicy,
    StorageType,
    ZoneType,
)


@dataclass(frozen=True)
class SKUSpec:
    """Lightweight value object representing a SKU's physical and regulatory attributes."""

    sku_id: str
    category: str
    unit_weight_kg: float
    width_cm: float
    height_cm: float
    depth_cm: float
    is_fragile: bool
    hazard_class: str
    temperature_requirement: str
    storage_type: str
    status: str = "ACTIVE"

    @property
    def unit_volume_m3(self) -> float:
        return calculate_unit_volume_m3(self.width_cm, self.height_cm, self.depth_cm)


@dataclass(frozen=True)
class LocationSpec:
    """Lightweight value object representing a Location and its enclosing Zone/Aisle/Warehouse state."""

    location_id: str
    warehouse_id: str
    warehouse_status: str
    zone_id: str
    zone_type: str
    zone_status: str
    temperature_type: str
    allowed_hazard_classes: frozenset[str]
    aisle_id: str
    aisle_is_blocked: bool
    x_coordinate: float
    y_coordinate: float
    depot_x: float
    depot_y: float
    width_cm: float
    height_cm: float
    depth_cm: float
    max_weight_kg: float
    max_volume_m3: float
    occupied_weight_kg: float
    occupied_volume_m3: float
    accessibility_type: str
    storage_policy: str
    is_blocked: bool
    status: str
    co_located_skus: tuple[SKUSpec, ...] = ()


@dataclass(frozen=True)
class PairwiseConflictRules:
    """Compiled conflict lookup tables built from CompatibilityRule records."""

    hazard_pairs: frozenset[tuple[str, str]] = field(default_factory=frozenset)
    category_pairs: frozenset[tuple[str, str]] = field(default_factory=frozenset)

    @classmethod
    def from_rule_dicts(cls, rules: list[dict[str, Any]]) -> PairwiseConflictRules:
        hz: set[tuple[str, str]] = set()
        cat: set[tuple[str, str]] = set()
        for r in rules:
            if not r.get("is_hard_constraint", True):
                continue
            a = str(r["primary_attribute"])
            b = str(r["conflicting_attribute"])
            if r["rule_type"] == "HAZARD_CO_LOCATION":
                hz.add((a, b))
                hz.add((b, a))
            elif r["rule_type"] == "CATEGORY_CO_LOCATION":
                cat.add((a, b))
                cat.add((b, a))
        return cls(hazard_pairs=frozenset(hz), category_pairs=frozenset(cat))


@dataclass
class CompatibilityResult:
    """Detailed outcome of evaluating all 9 hard constraints for a (SKU, Location, Quantity) tuple."""

    sku_id: str
    location_id: str
    quantity: int
    is_feasible: bool
    hard_constraints_checked: dict[str, bool]
    rejection_reasons: list[str]
    required_weight_kg: float
    required_volume_m3: float
    available_weight_kg: float
    available_volume_m3: float
    post_weight_utilization: float
    post_volume_utilization: float


def calculate_unit_volume_m3(width_cm: float, height_cm: float, depth_cm: float) -> float:
    """Compute SKU or bin cubic volume in m^3 from dimensions in cm (Section 8.3)."""
    if width_cm <= 0 or height_cm <= 0 or depth_cm <= 0:
        raise ValueError("Physical dimensions (width_cm, height_cm, depth_cm) must be > 0")
    return (float(width_cm) * float(height_cm) * float(depth_cm)) / 1_000_000.0


def calculate_available_capacity(
    max_weight_kg: float,
    occupied_weight_kg: float,
    max_volume_m3: float,
    occupied_volume_m3: float,
) -> tuple[float, float]:
    """Return remaining (available_weight_kg, available_volume_m3) clamped at >= 0.0 when reporting."""
    avail_wt = round(float(max_weight_kg) - float(occupied_weight_kg), 6)
    avail_vol = round(float(max_volume_m3) - float(occupied_volume_m3), 8)
    return avail_wt, avail_vol


class CompatibilityService:
    """Evaluates physical, environmental, hazard, capacity, and co-location hard constraints."""

    def __init__(
        self,
        conflict_rules: PairwiseConflictRules | None = None,
        enforce_dedicated_hazmat_zone: bool = True,
        storage_policy_override: str | None = None,
    ) -> None:
        self.conflict_rules = conflict_rules or PairwiseConflictRules()
        self.enforce_dedicated_hazmat_zone = enforce_dedicated_hazmat_zone
        self.storage_policy_override = storage_policy_override

    def are_two_skus_compatible(self, sku_a: SKUSpec, sku_b: SKUSpec) -> tuple[bool, list[str]]:
        """Check whether two SKUs may safely co-locate inside a SHARED_COMPATIBLE slot."""
        reasons: list[str] = []
        if (sku_a.hazard_class, sku_b.hazard_class) in self.conflict_rules.hazard_pairs:
            reasons.append(f"HAZARD_CONFLICT:{sku_a.hazard_class}_WITH_{sku_b.hazard_class}")
        if (sku_a.category, sku_b.category) in self.conflict_rules.category_pairs:
            reasons.append(f"CATEGORY_CONFLICT:{sku_a.category}_WITH_{sku_b.category}")
        if (sku_a.is_fragile and sku_b.category == SKUCategory.INDUSTRIAL_HARDWARE.value) or (
            sku_b.is_fragile and sku_a.category == SKUCategory.INDUSTRIAL_HARDWARE.value
        ):
            reasons.append("FRAGILE_WITH_HEAVY_HARDWARE_CONFLICT")
        return len(reasons) == 0, reasons

    def evaluate(
        self,
        sku: SKUSpec,
        location: LocationSpec,
        quantity: int = 1,
    ) -> CompatibilityResult:
        """Evaluate all 9 hard constraints and preserve every failed check as an explicit rejection reason."""
        if quantity <= 0:
            raise ValueError(f"Assigned quantity must be >= 1; got {quantity}")

        reasons: list[str] = []

        # 1. Warehouse & Zone Active
        wh_ok = location.warehouse_status == EntityStatus.ACTIVE.value
        if not wh_ok:
            reasons.append("WAREHOUSE_INACTIVE")

        zone_ok = location.zone_status == EntityStatus.ACTIVE.value
        if not zone_ok:
            reasons.append("ZONE_INACTIVE")

        # 2. Location & Aisle Active and Unblocked
        loc_active = location.status == EntityStatus.ACTIVE.value and not location.is_blocked
        if location.status != EntityStatus.ACTIVE.value:
            reasons.append("LOCATION_INACTIVE")
        if location.is_blocked:
            reasons.append("LOCATION_BLOCKED")
        if location.aisle_is_blocked:
            reasons.append("AISLE_BLOCKED")
        access_ok = loc_active and not location.aisle_is_blocked

        # 3. Dimensional Fit (Width, Height, Depth)
        w_ok = sku.width_cm <= location.width_cm + 1e-6
        h_ok = sku.height_cm <= location.height_cm + 1e-6
        d_ok = sku.depth_cm <= location.depth_cm + 1e-6
        if not w_ok:
            reasons.append("DIM_WIDTH_EXCEEDED")
        if not h_ok:
            reasons.append("DIM_HEIGHT_EXCEEDED")
        if not d_ok:
            reasons.append("DIM_DEPTH_EXCEEDED")
        dim_ok = w_ok and h_ok and d_ok

        # 4 & 5. Weight & Volume Capacity for Assigned Quantity
        req_wt = round(quantity * sku.unit_weight_kg, 6)
        req_vol = round(quantity * sku.unit_volume_m3, 8)
        avail_wt, avail_vol = calculate_available_capacity(
            location.max_weight_kg,
            location.occupied_weight_kg,
            location.max_volume_m3,
            location.occupied_volume_m3,
        )

        if sku.unit_weight_kg > location.max_weight_kg + 1e-6:
            reasons.append("UNIT_WEIGHT_EXCEEDS_LOCATION_MAX")
        wt_ok = req_wt <= avail_wt + 1e-5
        if not wt_ok and "UNIT_WEIGHT_EXCEEDS_LOCATION_MAX" not in reasons:
            reasons.append("WEIGHT_CAPACITY_INSUFFICIENT")

        if sku.unit_volume_m3 > location.max_volume_m3 + 1e-8:
            reasons.append("UNIT_VOLUME_EXCEEDS_LOCATION_MAX")
        vol_ok = req_vol <= avail_vol + 1e-7
        if not vol_ok and "UNIT_VOLUME_EXCEEDS_LOCATION_MAX" not in reasons:
            reasons.append("VOLUME_CAPACITY_INSUFFICIENT")

        # 6. Temperature Compatibility
        temp_ok = sku.temperature_requirement == location.temperature_type
        if not temp_ok:
            reasons.append(f"TEMPERATURE_MISMATCH:{sku.temperature_requirement}_VS_{location.temperature_type}")

        # 7. Hazard Allowance & Segregation
        haz_allowed = sku.hazard_class in location.allowed_hazard_classes
        if not haz_allowed:
            reasons.append(f"HAZARD_NOT_PERMITTED:{sku.hazard_class}")
        elif (
            self.enforce_dedicated_hazmat_zone
            and sku.hazard_class not in {HazardCode.NONE.value, HazardCode.LITHIUM_BATTERY.value}
            and location.zone_type != ZoneType.HAZMAT.value
        ):
            haz_allowed = False
            reasons.append(f"REGULATED_HAZMAT_REQUIRES_HAZMAT_ZONE:{sku.hazard_class}")

        # 8. Storage Policy & Pairwise Co-Location Compatibility
        effective_policy = self.storage_policy_override or location.storage_policy
        other_skus = [s for s in location.co_located_skus if s.sku_id != sku.sku_id]
        policy_ok = True
        coloc_ok = True

        if effective_policy == StoragePolicy.DEDICATED_SINGLE_SKU.value and other_skus:
            policy_ok = False
            reasons.append("DEDICATED_LOCATION_ALREADY_OCCUPIED")
        elif other_skus:
            for existing_sku in other_skus:
                pair_ok, pair_reasons = self.are_two_skus_compatible(sku, existing_sku)
                if not pair_ok:
                    coloc_ok = False
                    reasons.extend(pair_reasons)

        # 9. Ergonomic / Heavy High-Rack Safety Constraint
        handling_ok = True
        if (
            sku.unit_weight_kg > 25.0
            and location.accessibility_type == AccessibilityType.HIGH_RACK_FORKLIFT.value
            and sku.storage_type != StorageType.PALLET.value
        ):
            handling_ok = False
            reasons.append("HEAVY_NON_PALLET_HIGH_RACK_UNSAFE")

        checks = {
            "warehouse_active": wh_ok,
            "zone_active": zone_ok,
            "location_and_aisle_unblocked": access_ok,
            "dimensional_fit": dim_ok,
            "weight_capacity": wt_ok,
            "volume_capacity": vol_ok,
            "temperature_compatible": temp_ok,
            "hazard_compatible": haz_allowed,
            "storage_policy_and_colocation": policy_ok and coloc_ok,
            "ergonomic_handling_safe": handling_ok,
        }
        is_feasible = all(checks.values()) and len(reasons) == 0

        post_wt_util = (location.occupied_weight_kg + req_wt) / max(location.max_weight_kg, 1e-6)
        post_vol_util = (location.occupied_volume_m3 + req_vol) / max(location.max_volume_m3, 1e-9)

        return CompatibilityResult(
            sku_id=sku.sku_id,
            location_id=location.location_id,
            quantity=quantity,
            is_feasible=is_feasible,
            hard_constraints_checked=checks,
            rejection_reasons=reasons,
            required_weight_kg=round(req_wt, 4),
            required_volume_m3=round(req_vol, 6),
            available_weight_kg=round(avail_wt, 4),
            available_volume_m3=round(avail_vol, 6),
            post_weight_utilization=round(post_wt_util, 4),
            post_volume_utilization=round(post_vol_util, 4),
        )
