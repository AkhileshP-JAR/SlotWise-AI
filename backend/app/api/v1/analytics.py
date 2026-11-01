"""Executive overview, facility-scoped, and optimization performance analytics endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app.analytics.kpi_service import AnalyticsKPIService
from backend.app.config import EnvSettings
from backend.app.dependencies import (
    EntityNotFoundError,
    get_kpi_service,
    get_optimization_repo,
    get_pipeline_service,
    get_read_db,
    get_settings,
    get_warehouse_repo,
)
from backend.app.repositories import OptimizationRepository, WarehouseRepository
from backend.app.schemas.analytics import (
    AnalyticsOverviewResponse,
    OptimizationAnalyticsResponse,
    WarehouseAnalyticsResponse,
)
from backend.app.services.pipeline_service import DataPipelineService

router = APIRouter(prefix="/analytics", tags=["Operational Analytics"])


@router.get("/overview", response_model=AnalyticsOverviewResponse)
def get_analytics_overview(
    warehouse_id: str | None = Query(default=None, description="Optional warehouse filter."),
    sample_picking_orders: int = Query(default=250, ge=25, le=2000),
    db: Session = Depends(get_read_db),
    settings: EnvSettings = Depends(get_settings),
    kpi_service: AnalyticsKPIService = Depends(get_kpi_service),
    wh_repo: WarehouseRepository = Depends(get_warehouse_repo),
    pipeline: DataPipelineService = Depends(get_pipeline_service),
) -> AnalyticsOverviewResponse:
    """Compute real-time executive KPIs across warehouses, SKUs, optimization, picking, replenishment, and exceptions."""
    if warehouse_id and wh_repo.get_warehouse_detail(db, warehouse_id) is None:
        raise EntityNotFoundError("Warehouse", warehouse_id)

    kpis = kpi_service.get_overview_kpis(
        session=db,
        warehouse_id=warehouse_id,
        sample_picking_orders=sample_picking_orders,
    )
    gt_eval = kpi_service.evaluate_exception_detection_against_ground_truth(
        session=db,
        profile=settings.config_profile,
        data_root=pipeline.data_root,
    )
    return AnalyticsOverviewResponse(
        **kpis,
        ground_truth_evaluation=gt_eval,
    )


@router.get("/warehouse/{warehouse_id}", response_model=WarehouseAnalyticsResponse)
def get_warehouse_analytics(
    warehouse_id: str,
    sample_picking_orders: int = Query(default=250, ge=25, le=2000),
    db: Session = Depends(get_read_db),
    kpi_service: AnalyticsKPIService = Depends(get_kpi_service),
    wh_repo: WarehouseRepository = Depends(get_warehouse_repo),
) -> WarehouseAnalyticsResponse:
    """Compute facility-scoped KPIs, per-zone capacity utilization, and aisle corridor blockage metrics."""
    detail = wh_repo.get_warehouse_detail(session=db, warehouse_id=warehouse_id)
    if detail is None:
        raise EntityNotFoundError("Warehouse", warehouse_id)

    overview = kpi_service.get_overview_kpis(
        session=db,
        warehouse_id=warehouse_id,
        sample_picking_orders=sample_picking_orders,
    )
    zone_utilization = [
        {
            "zone_id": z.zone_id,
            "zone_name": z.zone_name,
            "zone_type": z.zone_type,
            "temperature_type": z.temperature_type,
            "permitted_hazards": z.permitted_hazards,
            "location_count": z.location_count,
            "occupied_location_count": z.occupied_location_count,
            "blocked_location_count": z.blocked_location_count,
            "location_utilization_pct": (
                round((z.occupied_location_count / z.location_count) * 100.0, 2) if z.location_count > 0 else 0.0
            ),
            "occupied_weight_kg": z.occupied_weight_kg,
            "max_weight_kg": z.max_weight_kg,
            "weight_utilization_pct": round((z.occupied_weight_kg / max(z.max_weight_kg, 1.0)) * 100.0, 2),
            "occupied_volume_m3": z.occupied_volume_m3,
            "max_volume_m3": z.max_volume_m3,
            "volume_utilization_pct": round((z.occupied_volume_m3 / max(z.max_volume_m3, 1e-6)) * 100.0, 2),
        }
        for z in detail.zones
    ]

    avg_cong = (
        round(sum(a.congestion_factor for a in detail.aisles) / len(detail.aisles), 3) if detail.aisles else 1.0
    )
    aisle_summary = {
        "total_aisles": len(detail.aisles),
        "blocked_aisles": sum(1 for a in detail.aisles if a.is_blocked),
        "blocked_aisle_ids": [a.aisle_id for a in detail.aisles if a.is_blocked],
        "average_congestion_factor": avg_cong,
    }

    return WarehouseAnalyticsResponse(
        warehouse_id=detail.warehouse_id,
        warehouse_name=detail.warehouse_name,
        overview=overview,
        zone_utilization=zone_utilization,
        aisle_summary=aisle_summary,
    )


@router.get("/optimization", response_model=OptimizationAnalyticsResponse)
def get_optimization_analytics(
    db: Session = Depends(get_read_db),
    settings: EnvSettings = Depends(get_settings),
    opt_repo: OptimizationRepository = Depends(get_optimization_repo),
    kpi_service: AnalyticsKPIService = Depends(get_kpi_service),
    pipeline: DataPipelineService = Depends(get_pipeline_service),
) -> OptimizationAnalyticsResponse:
    """Return optimization run history, assignment status counts, 6-component score averages, and F1 benchmark."""
    summary = opt_repo.get_optimization_analytics_summary(session=db)
    gt_eval = kpi_service.evaluate_exception_detection_against_ground_truth(
        session=db,
        profile=settings.config_profile,
        data_root=pipeline.data_root,
    )
    return OptimizationAnalyticsResponse(
        **summary,
        ground_truth_evaluation=gt_eval,
    )
