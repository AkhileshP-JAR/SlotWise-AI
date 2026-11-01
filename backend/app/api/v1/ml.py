"""SKU-level ML Demand Forecasting, MLflow experiment tracking, and forecast recommendations REST API."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app.config import EnvSettings
from backend.app.dependencies import (
    DomainValidationError,
    EntityNotFoundError,
    get_ml_forecaster_service,
    get_read_db,
    get_settings,
    get_warehouse_repo,
    get_write_db,
    require_write_access,
)
from backend.app.ml.forecaster import DemandForecastingService
from backend.app.repositories import WarehouseRepository
from backend.app.repositories.base import build_pagination_meta, clamp_pagination
from backend.app.schemas.common import PaginatedResponse
from backend.app.schemas.ml import (
    MLStatusResponse,
    MLTrainRequest,
    MLTrainResponse,
    SKUForecastOut,
)

router = APIRouter(prefix="/ml", tags=["ML Demand Forecasting & MLflow"])


@router.get("/status", response_model=MLStatusResponse)
def get_ml_status(
    profile: str | None = Query(default=None, description="Optional dataset profile tag."),
    settings: EnvSettings = Depends(get_settings),
    forecaster: DemandForecastingService = Depends(get_ml_forecaster_service),
) -> MLStatusResponse:
    """Return ML demand forecaster readiness, saved `.joblib` artifact path, and latest Naive vs. ML leaderboard."""
    data = forecaster.get_status(profile=profile or settings.config_profile)
    data["enabled"] = settings.enable_ml
    return MLStatusResponse.model_validate(data)


@router.post("/train", response_model=MLTrainResponse)
def train_ml_forecaster(
    payload: MLTrainRequest | None = None,
    actor: str = Depends(require_write_access),
    db: Session = Depends(get_write_db),
    settings: EnvSettings = Depends(get_settings),
    forecaster: DemandForecastingService = Depends(get_ml_forecaster_service),
) -> MLTrainResponse:
    """Train & evaluate Naive Baseline vs. ML models on chronological splits, log runs to MLflow, and save artifacts."""
    req = payload or MLTrainRequest()
    try:
        summary = forecaster.train_and_evaluate(
            session=db,
            profile=req.profile or settings.config_profile,
            forecast_horizon_days=req.forecast_horizon_days,
            train_ratio=req.train_ratio,
            actor=actor,
        )
    except ValueError as exc:
        raise DomainValidationError(str(exc)) from exc
    return MLTrainResponse.model_validate(summary)


@router.get("/forecasts", response_model=PaginatedResponse[SKUForecastOut])
def list_sku_forecasts(
    sku_id: str | None = Query(default=None, description="Optional exact SKU ID filter."),
    warehouse_id: str | None = Query(default=None, description="Optional active warehouse ID filter."),
    velocity_shift_only: bool = Query(
        default=False, description="Only return SKUs whose forecasted velocity tier differs from historical tier."
    ),
    surging_only: bool = Query(
        default=False, description="Only return SKUs exceeding the configured demand surge threshold."
    ),
    sort_by: Literal["sku_id", "forecast_daily_demand", "surge_ratio", "forecast_reorder_point"] = Query(
        default="surge_ratio"
    ),
    sort_order: Literal["asc", "desc"] = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=250),
    db: Session = Depends(get_read_db),
    settings: EnvSettings = Depends(get_settings),
    forecaster: DemandForecastingService = Depends(get_ml_forecaster_service),
    wh_repo: WarehouseRepository = Depends(get_warehouse_repo),
) -> PaginatedResponse[SKUForecastOut]:
    """List persisted SKU-level ML demand forecasts and slotting/replenishment recommendations."""
    if warehouse_id and wh_repo.get_warehouse_detail(db, warehouse_id) is None:
        raise EntityNotFoundError("Warehouse", warehouse_id)

    items = forecaster.list_forecasts(
        profile=settings.config_profile,
        sku_id=sku_id,
        warehouse_id=warehouse_id,
        velocity_shift_only=velocity_shift_only,
        surging_only=surging_only,
    )

    rev = sort_order == "desc"
    items_sorted = sorted(items, key=lambda x: (x.get(sort_by, 0), x["sku_id"]), reverse=rev)

    p, ps, start = clamp_pagination(page, page_size)
    total = len(items_sorted)
    sliced = [SKUForecastOut.model_validate(row) for row in items_sorted[start : start + ps]]

    return PaginatedResponse[SKUForecastOut](
        items=sliced,
        meta=build_pagination_meta(page=p, page_size=ps, total_items=total),
    )
