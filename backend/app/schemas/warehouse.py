"""Pydantic v2 schemas for Warehouses, Zones, Aisles, and Locations."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ZoneOut(BaseModel):
    """Warehouse storage zone representation with capacity and environmental constraints."""

    model_config = ConfigDict(from_attributes=True)

    zone_id: str
    warehouse_id: str
    zone_name: str
    zone_type: str
    temperature_type: str
    allowed_hazard_classes: str
    permitted_hazards: list[str] = []
    max_weight_kg: float
    max_volume_m3: float
    occupied_weight_kg: float = 0.0
    occupied_volume_m3: float = 0.0
    location_count: int = 0
    occupied_location_count: int = 0
    blocked_location_count: int = 0
    status: str


class AisleOut(BaseModel):
    """Physical aisle corridor geometry and blockage/congestion metadata."""

    model_config = ConfigDict(from_attributes=True)

    aisle_id: str
    zone_id: str
    warehouse_id: str
    aisle_number: int
    x_start: float
    y_start: float
    x_end: float
    y_end: float
    width_m: float
    is_blocked: bool
    congestion_factor: float
    status: str


class WarehouseSummaryOut(BaseModel):
    """Warehouse master record enriched with real-time location and capacity utilization."""

    model_config = ConfigDict(from_attributes=True)

    warehouse_id: str
    warehouse_name: str
    warehouse_type: str
    city: str
    state: str
    country: str
    total_area_sqft: float
    operating_hours: str
    daily_labor_minutes_capacity: float
    depot_x: float
    depot_y: float
    status: str
    created_at: datetime
    zone_count: int = 0
    aisle_count: int = 0
    blocked_aisle_count: int = 0
    total_locations: int = 0
    occupied_locations: int = 0
    blocked_locations: int = 0
    location_utilization_pct: float = 0.0
    total_max_weight_kg: float = 0.0
    total_current_weight_kg: float = 0.0
    weight_utilization_pct: float = 0.0
    total_max_volume_m3: float = 0.0
    total_current_volume_m3: float = 0.0
    volume_utilization_pct: float = 0.0


class WarehouseDetailOut(WarehouseSummaryOut):
    """Full warehouse detail including child zones and aisle corridors."""

    zones: list[ZoneOut] = []
    aisles: list[AisleOut] = []


class LocationOut(BaseModel):
    """Addressable warehouse storage slot representation."""

    model_config = ConfigDict(from_attributes=True)

    location_id: str
    warehouse_id: str | None = None
    zone_id: str
    zone_type: str | None = None
    temperature_type: str | None = None
    aisle_id: str
    aisle_is_blocked: bool = False
    rack_id: str
    shelf_level: int
    bin_id: str
    x_coordinate: float
    y_coordinate: float
    width_cm: float
    height_cm: float
    depth_cm: float
    max_weight_kg: float
    max_volume_m3: float
    current_weight_kg: float
    current_volume_m3: float
    available_weight_kg: float = 0.0
    available_volume_m3: float = 0.0
    weight_utilization_pct: float = 0.0
    volume_utilization_pct: float = 0.0
    accessibility_type: str
    storage_policy: str
    is_blocked: bool
    status: str
    active_sku_ids: list[str] = []


class LocationAssignmentBrief(BaseModel):
    """Brief view of an active or historical SKU assignment occupying a location."""

    model_config = ConfigDict(from_attributes=True)

    slot_assignment_id: str
    optimization_run_id: str | None
    sku_id: str
    assignment_status: str
    assigned_quantity: int
    allocated_weight_kg: float
    allocated_volume_m3: float
    assignment_score: float
    decision_version: int
    effective_from: datetime
    effective_to: datetime | None


class LocationDetailOut(LocationOut):
    """Detailed location view with active and historical assignments."""

    allowed_hazard_classes: list[str] = []
    active_assignments: list[LocationAssignmentBrief] = []
    historical_assignments: list[LocationAssignmentBrief] = []
