"""FastAPI dependency injection providers, transactional session scopes, domain errors, and write auth."""

from __future__ import annotations

import secrets
from collections.abc import Generator
from typing import Any

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.agents.assistant_service import DecisionAssistantService
from backend.app.analytics.kpi_service import AnalyticsKPIService
from backend.app.config import DATA_DIR, DomainConfig, EnvSettings, get_domain_config, get_env_settings
from backend.app.database import get_engine, get_session_factory
from backend.app.ml.forecaster import DemandForecastingService
from backend.app.optimization.candidates import CandidateGeneratorService
from backend.app.optimization.solver import SlottingOptimizerService
from backend.app.repositories import (
    OperationsRepository,
    OptimizationRepository,
    SKURepository,
    WarehouseRepository,
)
from backend.app.services.exception_engine import ExceptionDetectionService
from backend.app.services.pipeline_service import DataPipelineService
from backend.app.simulation.picking_simulator import PickingSimulationService
from backend.app.simulation.replenishment_simulator import ReplenishmentSimulationService
from backend.app.simulation.scenario_service import ScenarioComparisonService


class EntityNotFoundError(Exception):
    """Raised when a requested domain entity ID does not exist in the database."""

    def __init__(self, entity_type: str, entity_id: str) -> None:
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.message = f"{entity_type} '{entity_id}' was not found."
        super().__init__(self.message)


class DomainValidationError(Exception):
    """Raised when a business rule or lifecycle state-machine validation fails."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        self.message = message
        self.details = details
        super().__init__(message)


class ConflictError(Exception):
    """Raised when a requested mutation conflicts with current resource state."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        self.message = message
        self.details = details
        super().__init__(message)


def get_settings() -> EnvSettings:
    """Return runtime environment settings (overridable in test suites)."""
    return get_env_settings()


def get_active_domain_config(settings: EnvSettings = Depends(get_settings)) -> DomainConfig:
    """Return cached domain configuration for the active profile."""
    return get_domain_config(profile=settings.config_profile)


def get_read_db() -> Generator[Session, None, None]:
    """Yield a read-only database session that closes cleanly after request handling."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


def get_write_db() -> Generator[Session, None, None]:
    """Yield an atomic transactional database session that commits on success and rolls back on failure."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def require_write_access(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_actor: str | None = Header(default=None, alias="X-Actor"),
    settings: EnvSettings = Depends(get_settings),
) -> str:
    """Enforce optional write-token authorization on mutating endpoints and return sanitized actor ID."""
    expected_token = settings.api_write_token.strip()
    if expected_token:
        provided_token: str | None = None
        if x_api_key and x_api_key.strip():
            provided_token = x_api_key.strip()
        elif authorization and authorization.lower().startswith("bearer "):
            provided_token = authorization[7:].strip()

        if not provided_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Write authorization token is required via X-API-Key or Authorization: Bearer header.",
            )
        if not secrets.compare_digest(provided_token, expected_token):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid write authorization token.",
            )

    actor = (x_actor or "API_OPERATOR").strip()[:80]
    return actor or "API_OPERATOR"


def get_warehouse_repo() -> WarehouseRepository:
    return WarehouseRepository()


def get_sku_repo() -> SKURepository:
    return SKURepository()


def get_optimization_repo() -> OptimizationRepository:
    return OptimizationRepository()


def get_operations_repo() -> OperationsRepository:
    return OperationsRepository()


def get_pipeline_service() -> DataPipelineService:
    return DataPipelineService(data_root=DATA_DIR, engine=get_engine())


def get_candidate_service(
    config: DomainConfig = Depends(get_active_domain_config),
) -> CandidateGeneratorService:
    return CandidateGeneratorService(config=config)


def get_optimizer_service(
    config: DomainConfig = Depends(get_active_domain_config),
) -> SlottingOptimizerService:
    return SlottingOptimizerService(config=config)


def get_exception_service(
    config: DomainConfig = Depends(get_active_domain_config),
) -> ExceptionDetectionService:
    return ExceptionDetectionService(config=config)


def get_picking_service(
    config: DomainConfig = Depends(get_active_domain_config),
) -> PickingSimulationService:
    return PickingSimulationService(config=config)


def get_replenishment_service(
    config: DomainConfig = Depends(get_active_domain_config),
) -> ReplenishmentSimulationService:
    return ReplenishmentSimulationService(config=config)


def get_scenario_service(
    config: DomainConfig = Depends(get_active_domain_config),
) -> ScenarioComparisonService:
    return ScenarioComparisonService(config=config)


def get_kpi_service(
    config: DomainConfig = Depends(get_active_domain_config),
) -> AnalyticsKPIService:
    return AnalyticsKPIService(config=config)


def get_ml_forecaster_service(
    config: DomainConfig = Depends(get_active_domain_config),
) -> DemandForecastingService:
    return DemandForecastingService(config=config)


def get_assistant_service(
    config: DomainConfig = Depends(get_active_domain_config),
    ml_forecaster: DemandForecastingService = Depends(get_ml_forecaster_service),
) -> DecisionAssistantService:
    return DecisionAssistantService(config=config, ml_forecaster=ml_forecaster)
