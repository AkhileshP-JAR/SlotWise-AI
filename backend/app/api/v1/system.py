"""System status and immutable audit-event inspection endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.agents.llm_adapter import LLMProviderAdapter
from backend.app.config import DomainConfig, EnvSettings
from backend.app.dependencies import (
    get_active_domain_config,
    get_ml_forecaster_service,
    get_operations_repo,
    get_optimization_repo,
    get_pipeline_service,
    get_read_db,
    get_settings,
)
from backend.app.ml.forecaster import DemandForecastingService
from backend.app.repositories import OperationsRepository, OptimizationRepository
from backend.app.schemas.common import AuditEventOut, PaginatedResponse, SystemStatusResponse
from backend.app.services.audit_service import utc_now_naive
from backend.app.services.pipeline_service import DataPipelineService

router = APIRouter(prefix="/system", tags=["System & Audit"])


@router.get("/status", response_model=SystemStatusResponse)
def get_system_status(
    db: Session = Depends(get_read_db),
    settings: EnvSettings = Depends(get_settings),
    config: DomainConfig = Depends(get_active_domain_config),
    ops_repo: OperationsRepository = Depends(get_operations_repo),
    opt_repo: OptimizationRepository = Depends(get_optimization_repo),
    pipeline: DataPipelineService = Depends(get_pipeline_service),
    ml_forecaster: DemandForecastingService = Depends(get_ml_forecaster_service),
) -> SystemStatusResponse:
    """Return full operational system health, 21-table record counts, manifest version, and module readiness."""
    db.execute(text("SELECT 1"))
    dialect = db.get_bind().dialect.name
    counts = ops_repo.get_all_table_counts(db)
    total_records = sum(counts.values())

    manifest_info = pipeline.get_manifest(profile=settings.config_profile)
    manifest_payload = manifest_info.get("manifest") or {}
    dataset_version = manifest_payload.get("dataset_version")
    last_val = pipeline.get_last_validation_summary(profile=settings.config_profile)

    runs_page = opt_repo.list_runs(session=db, page=1, page_size=1)
    latest_run = runs_page.items[0].model_dump(mode="json") if runs_page.items else None

    ml_info = ml_forecaster.get_status(profile=settings.config_profile)
    eval_sum = ml_info.get("evaluation_summary") or {}
    llm_adapter = LLMProviderAdapter(settings=settings)

    return SystemStatusResponse(
        status="HEALTHY",
        version=config.project.version,
        config_version=config.project.config_version,
        active_profile=settings.config_profile,
        dataset_version=dataset_version,
        seed=config.project.seed,
        database_dialect=dialect,
        database_connected=True,
        write_auth_configured=bool(settings.api_write_token.strip()),
        table_counts=counts,
        total_records=total_records,
        last_validation=last_val,
        latest_optimization_run=latest_run,
        ml_status={
            "enabled": settings.enable_ml,
            "trained_artifacts_present": bool(ml_info.get("trained_artifacts_present")),
            "artifacts_directory": ml_forecaster.models_dir.as_posix(),
            "champion_model_name": eval_sum.get("champion_model_name"),
            "wape_improvement_vs_naive_pct": eval_sum.get("wape_improvement_vs_naive_pct"),
            "mlflow_run_id": eval_sum.get("champion_mlflow_run_id"),
        },
        assistant_status={
            "langgraph_enabled": True,
            "llm_provider": settings.llm_provider,
            "llm_credentials_available": llm_adapter.is_available,
            "execution_mode": (
                f"LLM_GROUNDED:{llm_adapter.provider_name}"
                if llm_adapter.is_available
                else "DETERMINISTIC_GROUNDED_FALLBACK"
            ),
        },
        timestamp=utc_now_naive(),
    )


@router.get("/audit-events", response_model=PaginatedResponse[AuditEventOut])
def list_audit_events(
    entity_type: str | None = Query(
        default=None, description="Filter by entity type (e.g., OPTIMIZATION_RUN, EXCEPTION)."
    ),
    entity_id: str | None = Query(default=None, description="Filter by target entity ID."),
    event_type: str | None = Query(default=None, description="Filter by audit event type."),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=250),
    db: Session = Depends(get_read_db),
    ops_repo: OperationsRepository = Depends(get_operations_repo),
) -> PaginatedResponse[AuditEventOut]:
    """List immutable operational audit events ordered newest first."""
    return ops_repo.list_audit_events(
        session=db,
        entity_type=entity_type,
        entity_id=entity_id,
        event_type=event_type,
        page=page,
        page_size=page_size,
    )
