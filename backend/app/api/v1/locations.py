"""Storage location browsing, capacity inspection, and occupancy history endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app.dependencies import EntityNotFoundError, get_read_db, get_warehouse_repo
from backend.app.repositories import WarehouseRepository
from backend.app.schemas.common import PaginatedResponse
from backend.app.schemas.warehouse import LocationDetailOut, LocationOut

router = APIRouter(prefix="/locations", tags=["Locations"])


@router.get("", response_model=PaginatedResponse[LocationOut])
def list_locations(
    warehouse_id: str | None = Query(default=None, description="Filter by warehouse ID (e.g., WH-001)."),
    zone_id: str | None = Query(default=None, description="Filter by zone ID (e.g., Z001)."),
    zone_type: str | None = Query(
        default=None, description="Filter by zone type (FAST_PICK, GENERAL, HAZMAT, COLD_STORAGE, OVERSIZE)."
    ),
    temperature_type: str | None = Query(
        default=None, description="Filter by temperature zone (AMBIENT, CHILLED, FROZEN)."
    ),
    storage_policy: str | None = Query(
        default=None, description="Filter by storage policy (DEDICATED_SINGLE_SKU, SHARED_COMPATIBLE)."
    ),
    accessibility_type: str | None = Query(
        default=None,
        description="Filter by ergonomic accessibility (GOLDEN_ZONE, FLOOR_LEVEL, HIGH_RACK, BULK_FLOOR).",
    ),
    is_blocked: bool | None = Query(
        default=None, description="Filter by location or enclosing aisle blockage status."
    ),
    occupied_only: bool | None = Query(
        default=None, description="True for occupied slots, False for empty slots."
    ),
    status: str | None = Query(
        default=None, description="Filter by entity status (ACTIVE, INACTIVE, MAINTENANCE)."
    ),
    sort_by: Literal[
        "location_id",
        "zone_id",
        "current_weight_kg",
        "max_weight_kg",
        "current_volume_m3",
        "max_volume_m3",
        "x_coordinate",
        "y_coordinate",
    ] = Query(default="location_id"),
    sort_order: Literal["asc", "desc"] = Query(default="asc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_read_db),
    repo: WarehouseRepository = Depends(get_warehouse_repo),
) -> PaginatedResponse[LocationOut]:
    """List warehouse storage locations with multi-attribute filtering, sorting, and active SKU occupancy."""
    return repo.list_locations(
        session=db,
        warehouse_id=warehouse_id,
        zone_id=zone_id,
        zone_type=zone_type,
        temperature_type=temperature_type,
        storage_policy=storage_policy,
        accessibility_type=accessibility_type,
        is_blocked=is_blocked,
        occupied_only=occupied_only,
        status=status,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )


@router.get("/{location_id}", response_model=LocationDetailOut)
def get_location(
    location_id: str,
    db: Session = Depends(get_read_db),
    repo: WarehouseRepository = Depends(get_warehouse_repo),
) -> LocationDetailOut:
    """Retrieve detailed storage location capacity, zone hazard allowances, and active/historical assignments."""
    detail = repo.get_location_detail(session=db, location_id=location_id)
    if detail is None:
        raise EntityNotFoundError("Location", location_id)
    return detail
