"""Pydantic v2 schemas for executive overview, warehouse, and optimization analytics."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class AnalyticsOverviewResponse(BaseModel):
    """Executive KPI summary calculated from persisted operational data plus read-only F1 evaluation."""

    warehouse_scope: str
    warehouse: dict[str, Any]
    sku: dict[str, Any]
    optimization: dict[str, Any]
    picking_simulated_estimates: dict[str, Any]
    replenishment: dict[str, Any]
    exceptions: dict[str, Any]
    ground_truth_evaluation: dict[str, Any]


class WarehouseAnalyticsResponse(BaseModel):
    """Scoped facility analytics with zone capacity utilization and aisle blockage breakdown."""

    warehouse_id: str
    warehouse_name: str
    overview: dict[str, Any]
    zone_utilization: list[dict[str, Any]]
    aisle_summary: dict[str, Any]


class OptimizationAnalyticsResponse(BaseModel):
    """Optimization run performance history, assignment lifecycle counts, and score breakdown averages."""

    total_runs: int
    latest_run: dict[str, Any] | None
    assignment_status_counts: dict[str, int]
    average_active_score_components: dict[str, float]
    run_history: list[dict[str, Any]]
    ground_truth_evaluation: dict[str, Any]
