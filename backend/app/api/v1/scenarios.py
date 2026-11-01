"""Baseline vs. Optimized slotting scenario comparison endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from backend.app.dependencies import (
    EntityNotFoundError,
    get_optimization_repo,
    get_read_db,
    get_scenario_service,
    get_warehouse_repo,
    get_write_db,
    require_write_access,
)
from backend.app.repositories import OptimizationRepository, WarehouseRepository
from backend.app.schemas.common import PaginatedResponse
from backend.app.schemas.optimization import ScenarioCreateRequest, ScenarioOut
from backend.app.simulation.scenario_service import ScenarioComparisonService

router = APIRouter(prefix="/scenarios", tags=["Scenario Comparison"])


@router.post("", response_model=ScenarioOut, status_code=status.HTTP_201_CREATED)
def create_scenario(
    payload: ScenarioCreateRequest,
    actor: str = Depends(require_write_access),
    db: Session = Depends(get_write_db),
    scn_service: ScenarioComparisonService = Depends(get_scenario_service),
    wh_repo: WarehouseRepository = Depends(get_warehouse_repo),
) -> ScenarioOut:
    """Create and persist a side-by-side comparison scenario between baseline slotting and an OptimizationRun."""
    if payload.warehouse_id and wh_repo.get_warehouse_detail(db, payload.warehouse_id) is None:
        raise EntityNotFoundError("Warehouse", payload.warehouse_id)

    try:
        scenario = scn_service.create_and_evaluate_scenario(
            session=db,
            optimization_run_id=payload.optimization_run_id,
            scenario_name=payload.scenario_name,
            description=payload.description,
            warehouse_id=payload.warehouse_id,
            max_orders=payload.max_orders,
            actor=actor,
        )
    except KeyError as exc:
        raise EntityNotFoundError("OptimizationRun", payload.optimization_run_id) from exc

    return ScenarioOut.model_validate(scenario)


@router.get("", response_model=PaginatedResponse[ScenarioOut])
def list_scenarios(
    warehouse_id: str | None = Query(default=None),
    optimization_run_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=250),
    db: Session = Depends(get_read_db),
    repo: OptimizationRepository = Depends(get_optimization_repo),
) -> PaginatedResponse[ScenarioOut]:
    """List persisted baseline vs. optimized comparison scenarios ordered newest first."""
    return repo.list_scenarios(
        session=db,
        warehouse_id=warehouse_id,
        optimization_run_id=optimization_run_id,
        page=page,
        page_size=page_size,
    )


@router.get("/{scenario_id}/comparison", response_model=ScenarioOut)
def get_scenario_comparison(
    scenario_id: str,
    db: Session = Depends(get_read_db),
    repo: OptimizationRepository = Depends(get_optimization_repo),
) -> ScenarioOut:
    """Retrieve baseline metrics, optimized metrics, and comparison deltas for a scenario."""
    scn = repo.get_scenario(session=db, scenario_id=scenario_id)
    if scn is None:
        raise EntityNotFoundError("Scenario", scenario_id)
    return ScenarioOut.model_validate(scn)
