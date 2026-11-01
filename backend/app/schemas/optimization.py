"""Pydantic v2 schemas for Slotting Optimization runs, versioned SlotAssignments, reasons, and Scenarios."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class OptimizationRunRequest(BaseModel):
    """Request payload to execute constrained SKU-to-location slotting optimization."""

    warehouse_id: str | None = Field(
        default=None, description="Optional warehouse filter (e.g., 'WH-001'); None optimizes all warehouses."
    )
    zone_id: str | None = Field(default=None, description="Optional zone filter (e.g., 'Z001').")
    sku_ids: list[str] | None = Field(default=None, description="Optional explicit SKU subset to re-slot.")
    strategy: Literal["auto", "ortools_cpsat", "greedy"] = Field(
        default="auto",
        description="Solver strategy: OR-Tools CP-SAT ('ortools_cpsat'), deterministic greedy+swap ('greedy'), or size-based ('auto').",
    )
    storage_policy_override: Literal["DEDICATED_SINGLE_SKU", "SHARED_COMPATIBLE"] | None = Field(
        default=None, description="Optional storage dedication policy override."
    )
    preserve_warehouse_affinity: bool = Field(
        default=True,
        description="Keep SKUs within their assigned regional warehouse facility when re-slotting.",
    )
    apply_immediately: bool = Field(
        default=False,
        description="If False (default governance workflow), creates PROPOSED assignments for operator review before approval.",
    )
    use_ml_forecasts: bool = Field(
        default=False,
        description="Opt-in: incorporate trained SKU-level ML demand forecasts into velocity scoring and desired quantities while enforcing all hard physical constraints.",
    )


class OptimizationRunOut(BaseModel):
    """Immutable optimization run execution summary and KPIs."""

    model_config = ConfigDict(from_attributes=True)

    run_id: str
    warehouse_scope: str
    zone_scope: str | None
    input_dataset_version: str
    config_version: str
    algorithm_name: str
    algorithm_version: str
    scoring_weights_json: dict[str, Any]
    seed: int
    sku_count: int
    candidate_count: int
    assignment_count: int
    unassigned_sku_count: int
    rejected_candidate_count: int
    exception_count: int
    kpi_results_json: dict[str, Any]
    execution_duration_ms: float
    run_status: str
    created_at: datetime


class AssignmentReasonOut(BaseModel):
    """Traceable evaluation rationale for a candidate location considered during optimization."""

    model_config = ConfigDict(from_attributes=True)

    reason_id: str
    optimization_run_id: str
    slot_assignment_id: str | None
    sku_id: str
    candidate_location_id: str
    is_selected: bool
    is_feasible: bool
    rank_position: int | None
    total_score: float
    score_breakdown_json: dict[str, Any]
    hard_constraints_checked_json: dict[str, bool]
    rejection_reasons_json: list[str]
    created_at: datetime


class SlotAssignmentOut(BaseModel):
    """Versioned SKU-to-location slotting decision record."""

    model_config = ConfigDict(from_attributes=True)

    slot_assignment_id: str
    optimization_run_id: str | None
    sku_id: str
    location_id: str
    warehouse_id: str
    assignment_status: str
    assigned_quantity: int
    allocated_weight_kg: float
    allocated_volume_m3: float
    assignment_score: float
    distance_score: float
    compatibility_score: float
    capacity_score: float
    velocity_score: float
    pick_frequency_score: float
    replenishment_score: float
    effective_from: datetime
    effective_to: datetime | None
    created_at: datetime
    decision_version: int
    decision_reason: str
    candidate_reasons: list[AssignmentReasonOut] = []


class AssignmentApprovalRequest(BaseModel):
    """Request payload to transition PROPOSED assignments of a run to ACTIVE."""

    assignment_ids: list[str] | None = Field(
        default=None, description="Optional subset of slot_assignment_ids; None approves all PROPOSED in run."
    )
    auto_resolve_exceptions: bool = Field(
        default=True,
        description="Re-scan operational exceptions and mark remediated slotting violations RESOLVED.",
    )


class AssignmentApprovalResponse(BaseModel):
    """Outcome summary of assignment approval and non-destructive historical supersession."""

    run_id: str
    approved_count: int
    superseded_count: int
    reconciled_locations: int
    exception_rescan: dict[str, Any] | None = None


class AssignmentRejectionRequest(BaseModel):
    """Request payload to reject PROPOSED recommendations while leaving ACTIVE assignments intact."""

    assignment_ids: list[str] | None = Field(default=None)
    reason: str = Field(default="Rejected during operator review.", min_length=3, max_length=500)


class AssignmentRejectionResponse(BaseModel):
    """Outcome summary of rejecting proposed slot assignments."""

    run_id: str
    rejected_count: int


class ScenarioCreateRequest(BaseModel):
    """Request payload to create and evaluate a Baseline vs. OptimizationRun comparison scenario."""

    optimization_run_id: str = Field(..., min_length=5, max_length=32)
    scenario_name: str | None = Field(default=None, max_length=140)
    description: str = Field(default="", max_length=1000)
    warehouse_id: str | None = Field(default=None)
    max_orders: int = Field(default=400, ge=10, le=5000)


class ScenarioOut(BaseModel):
    """Persisted baseline vs. optimized slotting scenario comparison record."""

    model_config = ConfigDict(from_attributes=True)

    scenario_id: str
    scenario_name: str
    description: str
    warehouse_id: str | None
    optimization_run_id: str
    parameters_json: dict[str, Any]
    baseline_metrics_json: dict[str, Any]
    optimized_metrics_json: dict[str, Any]
    comparison_delta_json: dict[str, Any]
    created_at: datetime
