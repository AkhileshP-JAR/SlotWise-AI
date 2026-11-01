"""Application settings and YAML profile loader for SlotWise AI."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = PROJECT_ROOT / "configs"
DATA_DIR = PROJECT_ROOT / "data"
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override dictionary into a copy of base dictionary."""
    merged = deepcopy(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


class ProjectMetaConfig(BaseModel):
    name: str = "SlotWise AI"
    version: str = "1.0.0"
    config_version: str = "cfg-2026.1"
    seed: int = 20261022


class AnomalyInjectionConfig(BaseModel):
    enabled: bool = True
    capacity_exceeded_count: int = Field(default=4, ge=0)
    incompatible_colocation_count: int = Field(default=3, ge=0)
    duplicate_active_assignment_count: int = Field(default=3, ge=0)
    blocked_aisle_pick_count: int = Field(default=4, ge=0)
    replenishment_overload_warehouses: int = Field(default=1, ge=0)
    unassignable_oversized_sku_count: int = Field(default=2, ge=0)
    nearly_exhausted_location_count: int = Field(default=4, ge=0)


class DatasetProfileConfig(BaseModel):
    profile: str = "small"
    reference_year: int = 2026
    observation_start_date: str = "2026-01-01"
    observation_days: int = Field(default=90, ge=7)
    warehouses: int = Field(default=2, ge=1)
    locations: int = Field(default=500, ge=10)
    skus: int = Field(default=250, ge=5)
    order_lines: int = Field(default=5000, ge=10)
    batch_size: int = Field(default=2500, ge=100)
    anomaly_injection: AnomalyInjectionConfig = Field(default_factory=AnomalyInjectionConfig)


class VelocityPercentilesConfig(BaseModel):
    fast_min_percentile: float = Field(default=0.80, gt=0.0, lt=1.0)
    medium_min_percentile: float = Field(default=0.35, gt=0.0, lt=1.0)


class ABCThresholdsConfig(BaseModel):
    a_cumulative_value_share: float = Field(default=0.80, gt=0.0, lt=1.0)
    b_cumulative_value_share: float = Field(default=0.95, gt=0.0, lt=1.0)


class XYZThresholdsConfig(BaseModel):
    x_max_cv: float = Field(default=0.50, gt=0.0)
    y_max_cv: float = Field(default=1.00, gt=0.0)


class DemandAnalyticsConfig(BaseModel):
    velocity_percentiles: VelocityPercentilesConfig = Field(default_factory=VelocityPercentilesConfig)
    abc_thresholds: ABCThresholdsConfig = Field(default_factory=ABCThresholdsConfig)
    xyz_cv_thresholds: XYZThresholdsConfig = Field(default_factory=XYZThresholdsConfig)
    safety_stock_z_score: float = Field(default=1.645, gt=0.0)
    default_lead_time_days: float = Field(default=5.0, gt=0.0)


class ScoringWeightsConfig(BaseModel):
    demand_velocity: float = Field(default=0.30, ge=0.0, le=1.0)
    pick_frequency: float = Field(default=0.20, ge=0.0, le=1.0)
    distance: float = Field(default=0.15, ge=0.0, le=1.0)
    capacity_utilization: float = Field(default=0.15, ge=0.0, le=1.0)
    replenishment_efficiency: float = Field(default=0.10, ge=0.0, le=1.0)
    zone_compatibility: float = Field(default=0.10, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_weight_sum(self) -> ScoringWeightsConfig:
        total = (
            self.demand_velocity
            + self.pick_frequency
            + self.distance
            + self.capacity_utilization
            + self.replenishment_efficiency
            + self.zone_compatibility
        )
        if abs(total - 1.0) > 1e-5:
            raise ValueError(f"Scoring weights must sum to 1.0; got {total:.6f}")
        return self


class ScoringConfig(BaseModel):
    weights: ScoringWeightsConfig = Field(default_factory=ScoringWeightsConfig)
    ideal_capacity_utilization: float = Field(default=0.78, gt=0.1, lt=1.0)


class OptimizationConfig(BaseModel):
    top_n_candidates: int = Field(default=15, ge=1, le=200)
    ortools_max_skus: int = Field(default=300, ge=1)
    ortools_time_limit_seconds: float = Field(default=15.0, gt=0.5)
    local_swap_max_iterations: int = Field(default=250, ge=0)
    default_storage_policy: str = "DEDICATED_SINGLE_SKU"
    auto_approve_assignments: bool = False


class PickingSimulationConfig(BaseModel):
    default_walk_speed_mps: float = Field(default=1.25, gt=0.1)
    pick_handling_seconds_per_line: float = Field(default=10.0, ge=0.0)
    pick_handling_seconds_per_unit: float = Field(default=1.5, ge=0.0)
    congestion_penalty_multiplier: float = Field(default=1.35, ge=1.0)
    two_opt_max_passes: int = Field(default=50, ge=1)


class ReplenishmentSimulationConfig(BaseModel):
    base_setup_minutes: float = Field(default=5.0, ge=0.0)
    travel_speed_mps: float = Field(default=1.0, gt=0.1)
    handling_minutes_per_100_units: float = Field(default=3.0, ge=0.0)
    high_frequency_warning_threshold: float = Field(default=12.0, gt=0.0)


class SimulationConfig(BaseModel):
    picking: PickingSimulationConfig = Field(default_factory=PickingSimulationConfig)
    replenishment: ReplenishmentSimulationConfig = Field(default_factory=ReplenishmentSimulationConfig)


class ExceptionsConfig(BaseModel):
    capacity_near_exhaustion_ratio: float = Field(default=0.92, gt=0.5, lt=1.0)
    excessive_replenishment_monthly_threshold: float = Field(default=12.0, gt=0.0)


class MLForecastingConfig(BaseModel):
    train_ratio: float = Field(default=0.80, gt=0.5, lt=0.95)
    warmup_lag_days: int = Field(default=14, ge=7, le=30)
    forecast_horizon_days: int = Field(default=14, ge=1, le=60)
    mlflow_experiment_name: str = "SlotWise_SKU_Demand_Forecasting"
    surge_threshold_ratio: float = Field(default=1.20, gt=1.0)
    decline_threshold_ratio: float = Field(default=0.80, gt=0.0, lt=1.0)


class DomainConfig(BaseModel):
    project: ProjectMetaConfig = Field(default_factory=ProjectMetaConfig)
    dataset: DatasetProfileConfig = Field(default_factory=DatasetProfileConfig)
    demand_analytics: DemandAnalyticsConfig = Field(default_factory=DemandAnalyticsConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    optimization: OptimizationConfig = Field(default_factory=OptimizationConfig)
    simulation: SimulationConfig = Field(default_factory=SimulationConfig)
    exceptions: ExceptionsConfig = Field(default_factory=ExceptionsConfig)
    ml_forecasting: MLForecastingConfig = Field(default_factory=MLForecastingConfig)


class EnvSettings(BaseSettings):
    """Environment-driven runtime settings."""

    model_config = SettingsConfigDict(
        env_prefix="SLOTWISE_",
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "local"
    database_url: str = f"sqlite:///{(DATA_DIR / 'slotwise.db').as_posix()}"
    config_profile: str = "small"
    default_seed: int = 20261022
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_write_token: str = ""
    backend_url: str = "http://localhost:8000"
    enable_ml: bool = True
    llm_provider: str = "none"
    llm_model: str = "llama-3.3-70b-versatile"
    llm_temperature: float = 0.0
    groq_api_key: str = ""
    openai_api_key: str = ""
    openai_base_url: str = ""


def load_domain_config(profile: str | None = None, config_dir: Path = CONFIGS_DIR) -> DomainConfig:
    """Load default.yaml merged with an optional dataset profile YAML (small, medium, full)."""
    default_path = config_dir / "default.yaml"
    if not default_path.exists():
        return DomainConfig()

    with default_path.open("r", encoding="utf-8") as fh:
        raw_config: dict[str, Any] = yaml.safe_load(fh) or {}

    selected_profile = (profile or EnvSettings().config_profile).lower().strip()
    profile_path = config_dir / f"{selected_profile}.yaml"
    if selected_profile != "default" and profile_path.exists():
        with profile_path.open("r", encoding="utf-8") as fh:
            override_cfg: dict[str, Any] = yaml.safe_load(fh) or {}
        raw_config = _deep_merge(raw_config, override_cfg)

    return DomainConfig.model_validate(raw_config)


@lru_cache(maxsize=1)
def get_env_settings() -> EnvSettings:
    """Return cached environment settings."""
    return EnvSettings()


@lru_cache(maxsize=8)
def get_domain_config(profile: str | None = None) -> DomainConfig:
    """Return cached domain configuration for a given profile."""
    return load_domain_config(profile=profile)
