"""Warehouse master-data and facility topology endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app.dependencies import EntityNotFoundError, get_read_db, get_warehouse_repo
from backend.app.repositories import WarehouseRepository
from backend.app.schemas.common import PaginatedResponse
from backend.app.schemas.warehouse import WarehouseDetailOut, WarehouseSummaryOut

router = APIRouter(prefix="/warehouses", tags=["Warehouses"])


@router.get("", response_model=PaginatedResponse[WarehouseSummaryOut])
def list_warehouses(
    status: str | None = Query(
        default=None, description="Filter by facility status (ACTIVE, INACTIVE, MAINTENANCE)."
    ),
    search: str | None = Query(default=None, description="Case-insensitive search by ID, name, city, or state."),
    sort_by: Literal[
        "warehouse_id",
        "warehouse_name",
        "city",
        "total_area_sqft",
        "daily_labor_minutes_capacity",
    ] = Query(default="warehouse_id"),
    sort_order: Literal["asc", "desc"] = Query(default="asc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=250),
    db: Session = Depends(get_read_db),
    repo: WarehouseRepository = Depends(get_warehouse_repo),
) -> PaginatedResponse[WarehouseSummaryOut]:
    """List warehouse facilities with aggregated location, weight, and volume utilization summaries."""
    return repo.list_warehouses(
        session=db,
        status=status,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )


@router.get("/{warehouse_id}", response_model=WarehouseDetailOut)
def get_warehouse(
    warehouse_id: str,
    db: Session = Depends(get_read_db),
    repo: WarehouseRepository = Depends(get_warehouse_repo),
) -> WarehouseDetailOut:
    """Retrieve a single warehouse enriched with child zones, permitted hazard classes, and aisle corridors."""
    detail = repo.get_warehouse_detail(session=db, warehouse_id=warehouse_id)
    if detail is None:
        raise EntityNotFoundError("Warehouse", warehouse_id)
    return detail
