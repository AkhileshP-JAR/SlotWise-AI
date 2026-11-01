"""Pick-path simulation and order picking operational analytics endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app.dependencies import (
    EntityNotFoundError,
    get_operations_repo,
    get_optimization_repo,
    get_picking_service,
    get_read_db,
    get_warehouse_repo,
)
from backend.app.repositories import OperationsRepository, OptimizationRepository, WarehouseRepository
from backend.app.schemas.operations import (
    PickingAnalyticsResponse,
    PickingSimulateRequest,
    PickingSimulateResponse,
)
from backend.app.simulation.picking_simulator import PickingSimulationService

router = APIRouter(prefix="/picking", tags=["Picking & Route Optimization"])


@router.post("/simulate", response_model=PickingSimulateResponse)
def simulate_picking(
    payload: PickingSimulateRequest,
    db: Session = Depends(get_read_db),
    picking_service: PickingSimulationService = Depends(get_picking_service),
    wh_repo: WarehouseRepository = Depends(get_warehouse_repo),
    opt_repo: OptimizationRepository = Depends(get_optimization_repo),
) -> PickingSimulateResponse:
    """Compare baseline slotting vs. optimized slotting & Nearest-Neighbor+2-Opt routing across identical orders."""
    if payload.warehouse_id and wh_repo.get_warehouse_detail(db, payload.warehouse_id) is None:
        raise EntityNotFoundError("Warehouse", payload.warehouse_id)
    if payload.optimization_run_id and opt_repo.get_run(db, payload.optimization_run_id) is None:
        raise EntityNotFoundError("OptimizationRun", payload.optimization_run_id)

    result = picking_service.compare_slotting_layouts(
        session=db,
        optimization_run_id=payload.optimization_run_id,
        warehouse_id=payload.warehouse_id,
        order_ids=payload.order_ids,
        max_orders=payload.max_orders,
    )
    return PickingSimulateResponse.model_validate(result)


@router.get("/analytics", response_model=PickingAnalyticsResponse)
def get_picking_analytics(
    warehouse_id: str | None = Query(default=None, description="Optional warehouse filter (e.g., WH-001)."),
    optimization_run_id: str | None = Query(
        default=None, description="Optional optimization run ID to compare against baseline."
    ),
    max_orders: int = Query(default=250, ge=10, le=2000),
    recent_orders_limit: int = Query(default=40, ge=5, le=200),
    db: Session = Depends(get_read_db),
    picking_service: PickingSimulationService = Depends(get_picking_service),
    ops_repo: OperationsRepository = Depends(get_operations_repo),
    wh_repo: WarehouseRepository = Depends(get_warehouse_repo),
    opt_repo: OptimizationRepository = Depends(get_optimization_repo),
) -> PickingAnalyticsResponse:
    """Return observed PickEvent telemetry combined with simulated slotting/routing savings and recent orders."""
    if warehouse_id and wh_repo.get_warehouse_detail(db, warehouse_id) is None:
        raise EntityNotFoundError("Warehouse", warehouse_id)
    if optimization_run_id and opt_repo.get_run(db, optimization_run_id) is None:
        raise EntityNotFoundError("OptimizationRun", optimization_run_id)

    observed, recent_orders = ops_repo.get_picking_telemetry_and_orders(
        session=db,
        warehouse_id=warehouse_id,
        recent_limit=recent_orders_limit,
    )
    sim_cmp = picking_service.compare_slotting_layouts(
        session=db,
        optimization_run_id=optimization_run_id,
        warehouse_id=warehouse_id,
        max_orders=max_orders,
    )
    return PickingAnalyticsResponse(
        warehouse_scope=warehouse_id or "ALL",
        observed_telemetry=observed,
        simulated_comparison=PickingSimulateResponse.model_validate(sim_cmp),
        recent_orders=recent_orders,
    )
