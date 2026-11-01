"""Pydantic v2 schemas for Picking simulation/analytics, Replenishment simulation/analytics, and Exceptions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PickingSimulateRequest(BaseModel):
    """Request payload to simulate baseline vs. optimized picking routes across identical orders."""

    warehouse_id: str | None = Field(default=None)
    optimization_run_id: str | None = Field(
        default=None, description="Optional run_id to compare against baseline; defaults to current ACTIVE layout."
    )
    order_ids: list[str] | None = Field(default=None, description="Optional explicit order IDs to simulate.")
    max_orders: int = Field(default=300, ge=1, le=5000)


class PickingSampleOrderOut(BaseModel):
    """Single order pick tour comparison with waypoint coordinates and stop sequences."""

    order_id: str
    warehouse_id: str
    line_count: int
    baseline_distance_m: float
    optimized_distance_m: float
    distance_saved_m: float
    baseline_time_seconds: float
    optimized_time_seconds: float
    baseline_sequence: list[str]
    optimized_sequence: list[str]
    baseline_waypoints: list[tuple[float, float]]
    optimized_waypoints: list[tuple[float, float]]


class PickingSimulateResponse(BaseModel):
    """Aggregated and sample-level picking path simulation results on identical order lines."""

    warehouse_scope: str
    optimization_run_id: str | None
    orders_evaluated: int
    order_lines_evaluated: int
    blocked_stops_excluded: int
    baseline_distance_m: float
    baseline_slotting_optimized_route_distance_m: float
    optimized_slotting_baseline_route_distance_m: float
    optimized_distance_m: float
    distance_saved_m: float
    percentage_improvement: float
    pure_slotting_distance_saved_m: float
    baseline_time_seconds: float
    optimized_time_seconds: float
    estimated_time_saved_seconds: float
    time_improvement_pct: float
    sample_orders: list[PickingSampleOrderOut]


class OrderBriefOut(BaseModel):
    """Brief outbound order summary for picking analytics order selector."""

    order_id: str
    warehouse_id: str
    order_date: datetime
    order_priority: str
    order_status: str
    customer_region: str
    line_count: int


class PickingAnalyticsResponse(BaseModel):
    """Observed pick-event operational telemetry combined with simulated slotting/routing savings."""

    warehouse_scope: str
    observed_telemetry: dict[str, Any]
    simulated_comparison: PickingSimulateResponse
    recent_orders: list[OrderBriefOut]


class ReplenishmentSimulateRequest(BaseModel):
    """Request payload for reorder-point replenishment and warehouse labor simulation."""

    warehouse_id: str | None = Field(default=None)
    optimization_run_id: str | None = Field(default=None)
    use_baseline_assignments: bool = Field(default=False)
    labor_capacity_multiplier: float = Field(
        default=1.0, gt=0.0, le=5.0, description="Multiplier on warehouse daily_labor_minutes_capacity."
    )
    persist_events: bool = Field(
        default=False, description="Persist simulated replenishment tasks to the database."
    )
    use_ml_forecasts: bool = Field(
        default=False,
        description="Opt-in: calculate replenishment triggers and target quantities from trained SKU-level ML demand forecasts.",
    )


class ReplenishmentWarehouseSummary(BaseModel):
    """Per-warehouse replenishment task count and labor capacity utilization."""

    warehouse_id: str
    task_count: int
    overloaded_task_count: int
    labor_minutes_required: float
    available_labor_minutes: float
    assigned_labor_minutes: float
    labor_utilization_pct: float
    is_overloaded: bool


class ReplenishmentTaskOut(BaseModel):
    """Simulated replenishment task item."""

    replenishment_id: str
    warehouse_id: str
    sku_id: str
    source_location_id: str | None
    destination_location_id: str
    required_quantity: int
    replenishment_quantity: int
    priority: str
    labor_minutes_required: float
    assigned_labor_minutes: float
    replenishment_status: str


class ReplenishmentSimulateResponse(BaseModel):
    """Complete replenishment workload simulation output."""

    warehouse_scope: str
    demand_source: str = "HISTORICAL_OBSERVED"
    forecast_adjusted_sku_count: int = 0
    total_requests: int
    total_labor_minutes_required: float
    total_available_labor_minutes: float
    overall_labor_utilization_pct: float
    overloaded_task_count: int
    overloaded_warehouses: list[str]
    warehouse_breakdown: list[ReplenishmentWarehouseSummary]
    tasks: list[ReplenishmentTaskOut]


class ReplenishmentOut(BaseModel):
    """Persisted replenishment queue record."""

    model_config = ConfigDict(from_attributes=True)

    replenishment_id: str
    warehouse_id: str
    sku_id: str
    source_location_id: str | None
    destination_location_id: str
    required_quantity: int
    replenishment_quantity: int
    priority: str
    labor_minutes_required: float
    assigned_labor_minutes: float
    replenishment_status: str
    created_at: datetime
    completed_at: datetime | None


class ReplenishmentAnalyticsResponse(BaseModel):
    """Persisted replenishment queue analytics, priority counts, and warehouse labor summaries."""

    warehouse_scope: str
    total_tasks: int
    active_tasks: int
    overloaded_tasks: int
    priority_distribution: dict[str, int]
    status_distribution: dict[str, int]
    warehouse_labor_summary: list[ReplenishmentWarehouseSummary]
    queue_page: list[ReplenishmentOut]
    total_queue_items: int
    page: int
    page_size: int


class ExceptionOut(BaseModel):
    """Operational, business-rule, data-quality, or optimization exception record."""

    model_config = ConfigDict(from_attributes=True)

    exception_id: str
    optimization_run_id: str | None
    category: str
    issue_type: str
    severity: str
    entity_type: str
    entity_id: str
    related_entity_ids_json: list[str]
    description: str
    detected_at: datetime
    resolution_status: str
    resolved_at: datetime | None
    resolution_note: str | None
    recommended_action: str
    ground_truth_flag: bool | None = Field(
        default=None,
        description="Read-only evaluation indicator populated strictly during presentation when ground truth exists.",
    )


class ExceptionResolveRequest(BaseModel):
    """Request payload to transition an exception's lifecycle state."""

    resolution_status: Literal["OPEN", "IN_REVIEW", "RESOLVED", "DISMISSED"] = Field(...)
    resolution_note: str | None = Field(default=None, max_length=1000)


class ExceptionScanRequest(BaseModel):
    """Request payload to trigger an operational exception detection scan."""

    optimization_run_id: str | None = Field(default=None)
    auto_resolve_remediated: bool = Field(default=True)


class ExceptionScanResponse(BaseModel):
    """Summary of detected and auto-resolved exceptions from an operational scan."""

    active_findings_count: int
    newly_created_count: int
    auto_resolved_count: int
    by_issue_type: dict[str, int]
    by_severity: dict[str, int]
