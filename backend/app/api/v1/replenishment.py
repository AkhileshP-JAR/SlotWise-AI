"""Forward-pick replenishment simulation and labor capacity analytics endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app.dependencies import (
    DomainValidationError,
    EntityNotFoundError,
    get_operations_repo,
    get_optimization_repo,
    get_read_db,
    get_replenishment_service,
    get_warehouse_repo,
    get_write_db,
    require_write_access,
)
from backend.app.repositories import OperationsRepository, OptimizationRepository, WarehouseRepository
from backend.app.schemas.operations import (
    ReplenishmentAnalyticsResponse,
    ReplenishmentSimulateRequest,
    ReplenishmentSimulateResponse,
)
from backend.app.simulation.replenishment_simulator import ReplenishmentSimulationService

router = APIRouter(prefix="/replenishment", tags=["Replenishment & Labor Workload"])


@router.post("/simulate", response_model=ReplenishmentSimulateResponse)
def simulate_replenishment(
    payload: ReplenishmentSimulateRequest,
    actor: str = Depends(require_write_access),
    db: Session = Depends(get_write_db),
    rep_service: ReplenishmentSimulationService = Depends(get_replenishment_service),
    wh_repo: WarehouseRepository = Depends(get_warehouse_repo),
    opt_repo: OptimizationRepository = Depends(get_optimization_repo),
) -> ReplenishmentSimulateResponse:
    """Simulate reorder-point replenishment triggers, warehouse labor budget allocation, and overload flags."""
    if payload.warehouse_id and wh_repo.get_warehouse_detail(db, payload.warehouse_id) is None:
        raise EntityNotFoundError("Warehouse", payload.warehouse_id)
    if payload.optimization_run_id and opt_repo.get_run(db, payload.optimization_run_id) is None:
        raise EntityNotFoundError("OptimizationRun", payload.optimization_run_id)

    try:
        result = rep_service.simulate_replenishment(
            session=db,
            warehouse_id=payload.warehouse_id,
            optimization_run_id=payload.optimization_run_id,
            use_baseline_assignments=payload.use_baseline_assignments,
            labor_capacity_multiplier=payload.labor_capacity_multiplier,
            use_ml_forecasts=payload.use_ml_forecasts,
            persist_events=payload.persist_events,
            actor=actor,
        )
    except ValueError as exc:
        raise DomainValidationError(str(exc)) from exc

    return ReplenishmentSimulateResponse.model_validate(result)


@router.get("/analytics", response_model=ReplenishmentAnalyticsResponse)
def get_replenishment_analytics(
    warehouse_id: str | None = Query(default=None),
    priority: str | None = Query(
        default=None, description="Filter queue by priority (CRITICAL, HIGH, NORMAL, LOW)."
    ),
    replenishment_status: str | None = Query(
        default=None,
        description="Filter queue by status (PENDING, IN_PROGRESS, COMPLETED, OVERLOADED, CANCELLED).",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=250),
    db: Session = Depends(get_read_db),
    ops_repo: OperationsRepository = Depends(get_operations_repo),
    wh_repo: WarehouseRepository = Depends(get_warehouse_repo),
) -> ReplenishmentAnalyticsResponse:
    """Return persisted replenishment queue distributions, warehouse labor utilization, and paginated tasks."""
    if warehouse_id and wh_repo.get_warehouse_detail(db, warehouse_id) is None:
        raise EntityNotFoundError("Warehouse", warehouse_id)

    data = ops_repo.get_replenishment_analytics_data(
        session=db,
        warehouse_id=warehouse_id,
        priority=priority,
        replenishment_status=replenishment_status,
        page=page,
        page_size=page_size,
    )
    return ReplenishmentAnalyticsResponse.model_validate(data)
