"""Pydantic v2 schemas for SKU-level ML Demand Forecasting, MLflow experiment tracking, and forecast recommendations."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class MLTrainRequest(BaseModel):
    """Request payload to train and evaluate SKU-level demand forecasting models on chronological splits."""

    profile: Literal["small", "medium", "full"] | None = Field(
        default=None,
        description="Optional dataset profile tag for artifact naming; defaults to active server profile.",
    )
    forecast_horizon_days: int = Field(
        default=14,
        ge=1,
        le=60,
        description="Forward multi-step forecast horizon in calendar days.",
    )
    train_ratio: float = Field(
        default=0.80,
        ge=0.60,
        le=0.90,
        description="Chronological fraction of observation days allocated to training+warmup vs. holdout testing.",
    )


class MLModelMetricsOut(BaseModel):
    """Holdout chronological test metrics comparing Naive Baselines and ML regressors."""

    model_name: str
    model_family: Literal["NAIVE_BASELINE", "MACHINE_LEARNING"]
    mlflow_run_id: str | None = None
    daily_mae: float
    daily_rmse: float
    daily_wape_pct: float
    horizon_mae: float
    horizon_rmse: float
    horizon_wape_pct: float
    is_champion: bool = False


class ChronologicalSplitInfo(BaseModel):
    """Strict chronological train/test split metadata proving zero temporal overlap."""

    observation_start_date: str
    observation_end_date: str
    warmup_start_date: str
    warmup_end_date: str
    train_start_date: str
    train_end_date: str
    test_start_date: str
    test_end_date: str
    total_calendar_days: int
    warmup_days: int
    train_days: int
    test_days: int
    active_sku_count: int
    train_rows: int
    test_rows: int
    chronological_order_verified: bool


class SKUForecastOut(BaseModel):
    """SKU-level multi-step ML demand forecast and operational slotting/replenishment recommendations."""

    sku_id: str
    sku_name: str
    category: str
    active_warehouse_id: str | None = None
    active_location_id: str | None = None
    forecast_horizon_days: int
    historical_daily_demand: float
    forecast_daily_demand: float
    forecast_total_units: float
    forecast_std_dev: float
    surge_ratio: float
    historical_velocity: str
    forecast_velocity: str
    forecast_velocity_percentile: float
    velocity_tier_changed: bool
    historical_reorder_point: float
    forecast_reorder_point: float
    slotting_recommendation: str
    replenishment_recommendation: str
    daily_forecast_trajectory: list[dict[str, Any]] = []


class MLTrainResponse(BaseModel):
    """Complete training, chronological evaluation, MLflow tracking, and artifact persistence summary."""

    status: str
    profile: str
    seed: int
    champion_model_name: str
    baseline_reference_model: str
    wape_improvement_vs_naive_pct: float
    mae_improvement_vs_naive_pct: float
    mlflow_experiment_name: str
    mlflow_tracking_uri: str
    champion_mlflow_run_id: str | None
    artifact_joblib_path: str
    evaluation_report_path: str
    forecasts_path: str
    split_info: ChronologicalSplitInfo
    leaderboard: list[MLModelMetricsOut]
    surging_sku_count: int
    velocity_shift_sku_count: int
    top_surging_skus: list[SKUForecastOut]
    trained_at: str


class MLStatusResponse(BaseModel):
    """Current ML demand forecasting subsystem readiness, latest leaderboard, and artifact metadata."""

    enabled: bool
    trained_artifacts_present: bool
    profile: str
    artifact_joblib_path: str
    mlflow_tracking_uri: str
    evaluation_summary: dict[str, Any] | None = None
