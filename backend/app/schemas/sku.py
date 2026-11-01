"""Pydantic v2 schemas for SKUs, analytical SKUProfiles, demand trends, and candidate slot evaluations."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SKUProfileOut(BaseModel):
    """Analytical demand velocity, ABC/XYZ, and reorder-point profile for a SKU."""

    model_config = ConfigDict(from_attributes=True)

    profile_id: str
    sku_id: str
    average_daily_demand: float
    demand_std_dev: float
    demand_velocity: str
    velocity_percentile: float
    pick_frequency: float
    total_units_sold: int
    total_order_lines: int
    abc_class: str
    xyz_class: str
    turnover_ratio: float
    safety_stock: float
    reorder_point: float
    target_days_of_supply: int
    current_on_hand_qty: int
    replenishment_frequency: float
    peak_demand_multiplier: float
    effective_from: datetime
    effective_to: datetime | None


class SKUAssignmentBrief(BaseModel):
    """Active or historical slot assignment associated with a SKU."""

    model_config = ConfigDict(from_attributes=True)

    slot_assignment_id: str
    optimization_run_id: str | None
    location_id: str
    warehouse_id: str
    assignment_status: str
    assigned_quantity: int
    allocated_weight_kg: float
    allocated_volume_m3: float
    assignment_score: float
    decision_version: int
    decision_reason: str
    effective_from: datetime
    effective_to: datetime | None


class DailyDemandPoint(BaseModel):
    """Daily historical demand aggregation point for SKU trend charts."""

    order_date: str
    units_ordered: int
    order_lines: int
    revenue: float


class SKUOut(BaseModel):
    """Master SKU record enriched with active analytical summary and current location."""

    model_config = ConfigDict(from_attributes=True)

    sku_id: str
    sku_name: str
    category: str
    unit_price: float
    unit_weight_kg: float
    width_cm: float
    height_cm: float
    depth_cm: float
    unit_volume_m3: float
    is_fragile: bool
    hazard_class: str
    temperature_requirement: str
    storage_type: str
    supplier_id: str | None
    status: str
    demand_velocity: str | None = None
    abc_class: str | None = None
    xyz_class: str | None = None
    average_daily_demand: float | None = None
    pick_frequency: float | None = None
    current_on_hand_qty: int | None = None
    reorder_point: float | None = None
    active_location_id: str | None = None
    active_warehouse_id: str | None = None


class SKUDetailOut(SKUOut):
    """Comprehensive SKU intelligence payload with profile, demand history, and assignment lineage."""

    supplier_name: str | None = None
    supplier_lead_time_days: float | None = None
    profile: SKUProfileOut | None = None
    active_assignments: list[SKUAssignmentBrief] = []
    assignment_history: list[SKUAssignmentBrief] = []
    demand_trend: list[DailyDemandPoint] = []


class ScoreBreakdownOut(BaseModel):
    """Explainable normalized 6-component score breakdown in [0.0, 1.0]."""

    total_score: float
    velocity_score: float
    pick_frequency_score: float
    distance_score: float
    capacity_score: float
    replenishment_score: float
    compatibility_score: float
    dock_distance_m: float
    explanation: str


class EvaluatedCandidateOut(BaseModel):
    """Evaluated candidate slot preserving feasibility, score breakdown, and rejection reasons."""

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
    rank_position: int | None = None
    score_breakdown: ScoreBreakdownOut
    hard_constraints_checked: dict[str, bool]
    rejection_reasons: list[str]


class SKUCandidatesResponse(BaseModel):
    """Complete candidate slot generation and constraint evaluation output for a SKU."""

    sku_id: str
    warehouse_scope: str
    zone_scope: str | None
    total_locations_evaluated: int
    total_feasible_count: int
    total_rejected_count: int
    rejection_reason_counts: dict[str, int]
    top_feasible: list[EvaluatedCandidateOut]
    rejected_samples: list[EvaluatedCandidateOut]
