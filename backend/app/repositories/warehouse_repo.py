"""SQLAlchemy 2.x repository for Warehouses, Zones, Aisles, and Locations."""

from __future__ import annotations

from typing import Any

from sqlalchemy import asc, desc, func, or_, select
from sqlalchemy.orm import Session

from backend.app.models import (
    Aisle,
    Location,
    SlotAssignment,
    Warehouse,
    Zone,
    ZoneHazardAllowance,
)
from backend.app.models.enums import AssignmentStatus
from backend.app.repositories.base import build_pagination_meta, clamp_pagination
from backend.app.schemas.common import PaginatedResponse
from backend.app.schemas.warehouse import (
    AisleOut,
    LocationAssignmentBrief,
    LocationDetailOut,
    LocationOut,
    WarehouseDetailOut,
    WarehouseSummaryOut,
    ZoneOut,
)
from backend.app.services.compatibility import calculate_available_capacity


class WarehouseRepository:
    """Data-access repository for facility, zone, aisle, and storage-slot queries."""

    def list_warehouses(
        self,
        session: Session,
        status: str | None = None,
        search: str | None = None,
        sort_by: str = "warehouse_id",
        sort_order: str = "asc",
        page: int = 1,
        page_size: int = 25,
    ) -> PaginatedResponse[WarehouseSummaryOut]:
        """List warehouses with aggregated zone, aisle, location, and capacity utilization metrics."""
        safe_page, safe_size, offset = clamp_pagination(page, page_size)

        stmt = select(Warehouse)
        if status:
            stmt = stmt.where(Warehouse.status == status.upper())
        if search:
            q = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    Warehouse.warehouse_id.ilike(q),
                    Warehouse.warehouse_name.ilike(q),
                    Warehouse.city.ilike(q),
                    Warehouse.state.ilike(q),
                )
            )

        total_items = int(session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)

        sort_map = {
            "warehouse_id": Warehouse.warehouse_id,
            "warehouse_name": Warehouse.warehouse_name,
            "city": Warehouse.city,
            "total_area_sqft": Warehouse.total_area_sqft,
            "daily_labor_minutes_capacity": Warehouse.daily_labor_minutes_capacity,
        }
        col = sort_map.get(sort_by, Warehouse.warehouse_id)
        order_fn = desc if sort_order.lower() == "desc" else asc
        rows = session.scalars(
            stmt.order_by(order_fn(col), Warehouse.warehouse_id).offset(offset).limit(safe_size)
        ).all()

        items = [self._build_warehouse_summary(session, w) for w in rows]
        return PaginatedResponse(
            items=items,
            meta=build_pagination_meta(safe_page, safe_size, total_items),
        )

    def get_warehouse_detail(self, session: Session, warehouse_id: str) -> WarehouseDetailOut | None:
        """Retrieve a single warehouse enriched with child zones, hazard allowances, and aisles."""
        wh = session.get(Warehouse, warehouse_id)
        if wh is None:
            return None

        summary = self._build_warehouse_summary(session, wh)
        zones_orm = session.scalars(
            select(Zone).where(Zone.warehouse_id == warehouse_id).order_by(Zone.zone_id)
        ).all()
        aisles_orm = session.scalars(
            select(Aisle).where(Aisle.warehouse_id == warehouse_id).order_by(Aisle.aisle_id)
        ).all()

        zone_ids = [z.zone_id for z in zones_orm]
        allowances: dict[str, list[str]] = {}
        if zone_ids:
            for al in session.scalars(
                select(ZoneHazardAllowance).where(ZoneHazardAllowance.zone_id.in_(zone_ids))
            ).all():
                allowances.setdefault(al.zone_id, []).append(al.hazard_class_id)

        locs_by_zone: dict[str, list[Location]] = {}
        if zone_ids:
            for loc in session.scalars(select(Location).where(Location.zone_id.in_(zone_ids))).all():
                locs_by_zone.setdefault(loc.zone_id, []).append(loc)

        zones_out: list[ZoneOut] = []
        for z in zones_orm:
            z_locs = locs_by_zone.get(z.zone_id, [])
            occ_wt = round(sum(float(loc.current_weight_kg) for loc in z_locs), 2)
            occ_vol = round(sum(float(loc.current_volume_m3) for loc in z_locs), 4)
            occ_cnt = sum(
                1 for loc in z_locs if float(loc.current_weight_kg) > 0.0 or float(loc.current_volume_m3) > 0.0
            )
            blk_cnt = sum(1 for loc in z_locs if bool(loc.is_blocked))
            zones_out.append(
                ZoneOut(
                    zone_id=z.zone_id,
                    warehouse_id=z.warehouse_id,
                    zone_name=z.zone_name,
                    zone_type=z.zone_type,
                    temperature_type=z.temperature_type,
                    allowed_hazard_classes=z.allowed_hazard_classes,
                    permitted_hazards=sorted(allowances.get(z.zone_id, [])),
                    max_weight_kg=float(z.max_weight_kg),
                    max_volume_m3=float(z.max_volume_m3),
                    occupied_weight_kg=occ_wt,
                    occupied_volume_m3=occ_vol,
                    location_count=len(z_locs),
                    occupied_location_count=occ_cnt,
                    blocked_location_count=blk_cnt,
                    status=z.status,
                )
            )

        aisles_out = [AisleOut.model_validate(a) for a in aisles_orm]
        return WarehouseDetailOut(
            **summary.model_dump(),
            zones=zones_out,
            aisles=aisles_out,
        )

    def list_locations(
        self,
        session: Session,
        warehouse_id: str | None = None,
        zone_id: str | None = None,
        zone_type: str | None = None,
        temperature_type: str | None = None,
        storage_policy: str | None = None,
        accessibility_type: str | None = None,
        is_blocked: bool | None = None,
        occupied_only: bool | None = None,
        status: str | None = None,
        sort_by: str = "location_id",
        sort_order: str = "asc",
        page: int = 1,
        page_size: int = 50,
    ) -> PaginatedResponse[LocationOut]:
        """List storage locations with multi-attribute filtering, sorting, and active SKU occupancy."""
        safe_page, safe_size, offset = clamp_pagination(page, page_size, max_page_size=500)

        stmt = (
            select(Location, Zone, Aisle)
            .join(Zone, Location.zone_id == Zone.zone_id)
            .join(Aisle, Location.aisle_id == Aisle.aisle_id)
        )
        if warehouse_id:
            stmt = stmt.where(Zone.warehouse_id == warehouse_id)
        if zone_id:
            stmt = stmt.where(Location.zone_id == zone_id)
        if zone_type:
            stmt = stmt.where(Zone.zone_type == zone_type.upper())
        if temperature_type:
            stmt = stmt.where(Zone.temperature_type == temperature_type.upper())
        if storage_policy:
            stmt = stmt.where(Location.storage_policy == storage_policy.upper())
        if accessibility_type:
            stmt = stmt.where(Location.accessibility_type == accessibility_type.upper())
        if is_blocked is not None:
            if is_blocked:
                stmt = stmt.where(or_(Location.is_blocked.is_(True), Aisle.is_blocked.is_(True)))
            else:
                stmt = stmt.where(Location.is_blocked.is_(False), Aisle.is_blocked.is_(False))
        if occupied_only is True:
            stmt = stmt.where(or_(Location.current_weight_kg > 0.0, Location.current_volume_m3 > 0.0))
        elif occupied_only is False:
            stmt = stmt.where(Location.current_weight_kg <= 0.0, Location.current_volume_m3 <= 0.0)
        if status:
            stmt = stmt.where(Location.status == status.upper())

        total_items = int(session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)

        sort_map: dict[str, Any] = {
            "location_id": Location.location_id,
            "zone_id": Location.zone_id,
            "current_weight_kg": Location.current_weight_kg,
            "max_weight_kg": Location.max_weight_kg,
            "current_volume_m3": Location.current_volume_m3,
            "max_volume_m3": Location.max_volume_m3,
            "x_coordinate": Location.x_coordinate,
            "y_coordinate": Location.y_coordinate,
        }
        col = sort_map.get(sort_by, Location.location_id)
        order_fn = desc if sort_order.lower() == "desc" else asc
        rows = session.execute(
            stmt.order_by(order_fn(col), Location.location_id).offset(offset).limit(safe_size)
        ).all()

        loc_ids = [r[0].location_id for r in rows]
        active_skus_by_loc: dict[str, list[str]] = {}
        if loc_ids:
            for assn in session.scalars(
                select(SlotAssignment)
                .where(
                    SlotAssignment.location_id.in_(loc_ids),
                    SlotAssignment.assignment_status == AssignmentStatus.ACTIVE.value,
                )
                .order_by(SlotAssignment.sku_id)
            ).all():
                active_skus_by_loc.setdefault(assn.location_id, []).append(assn.sku_id)

        items = [
            self._to_location_out(loc, zone, aisle, active_skus_by_loc.get(loc.location_id, []))
            for loc, zone, aisle in rows
        ]
        return PaginatedResponse(
            items=items,
            meta=build_pagination_meta(safe_page, safe_size, total_items),
        )

    def get_location_detail(self, session: Session, location_id: str) -> LocationDetailOut | None:
        """Retrieve a storage location with enclosing zone hazards and active/historical assignments."""
        row = session.execute(
            select(Location, Zone, Aisle)
            .join(Zone, Location.zone_id == Zone.zone_id)
            .join(Aisle, Location.aisle_id == Aisle.aisle_id)
            .where(Location.location_id == location_id)
        ).first()
        if row is None:
            return None
        loc, zone, aisle = row

        hazards = sorted(
            session.scalars(
                select(ZoneHazardAllowance.hazard_class_id).where(ZoneHazardAllowance.zone_id == zone.zone_id)
            ).all()
        )
        assns = session.scalars(
            select(SlotAssignment)
            .where(SlotAssignment.location_id == location_id)
            .order_by(SlotAssignment.decision_version.desc(), SlotAssignment.slot_assignment_id.desc())
        ).all()

        active_assns = [
            LocationAssignmentBrief.model_validate(a)
            for a in assns
            if a.assignment_status == AssignmentStatus.ACTIVE.value
        ]
        hist_assns = [
            LocationAssignmentBrief.model_validate(a)
            for a in assns
            if a.assignment_status != AssignmentStatus.ACTIVE.value
        ]
        base_out = self._to_location_out(loc, zone, aisle, [a.sku_id for a in active_assns])
        return LocationDetailOut(
            **base_out.model_dump(),
            allowed_hazard_classes=hazards,
            active_assignments=active_assns,
            historical_assignments=hist_assns,
        )

    @staticmethod
    def _to_location_out(loc: Location, zone: Zone, aisle: Aisle, active_sku_ids: list[str]) -> LocationOut:
        avail_wt, avail_vol = calculate_available_capacity(
            loc.max_weight_kg, loc.current_weight_kg, loc.max_volume_m3, loc.current_volume_m3
        )
        wt_util = round((float(loc.current_weight_kg) / max(float(loc.max_weight_kg), 1e-6)) * 100.0, 2)
        vol_util = round((float(loc.current_volume_m3) / max(float(loc.max_volume_m3), 1e-9)) * 100.0, 2)
        return LocationOut(
            location_id=loc.location_id,
            warehouse_id=zone.warehouse_id,
            zone_id=loc.zone_id,
            zone_type=zone.zone_type,
            temperature_type=zone.temperature_type,
            aisle_id=loc.aisle_id,
            aisle_is_blocked=bool(aisle.is_blocked),
            rack_id=loc.rack_id,
            shelf_level=int(loc.shelf_level),
            bin_id=loc.bin_id,
            x_coordinate=float(loc.x_coordinate),
            y_coordinate=float(loc.y_coordinate),
            width_cm=float(loc.width_cm),
            height_cm=float(loc.height_cm),
            depth_cm=float(loc.depth_cm),
            max_weight_kg=float(loc.max_weight_kg),
            max_volume_m3=float(loc.max_volume_m3),
            current_weight_kg=float(loc.current_weight_kg),
            current_volume_m3=float(loc.current_volume_m3),
            available_weight_kg=max(0.0, round(avail_wt, 4)),
            available_volume_m3=max(0.0, round(avail_vol, 6)),
            weight_utilization_pct=wt_util,
            volume_utilization_pct=vol_util,
            accessibility_type=loc.accessibility_type,
            storage_policy=loc.storage_policy,
            is_blocked=bool(loc.is_blocked),
            status=loc.status,
            active_sku_ids=active_sku_ids,
        )

    @staticmethod
    def _build_warehouse_summary(session: Session, wh: Warehouse) -> WarehouseSummaryOut:
        zones = session.scalars(select(Zone).where(Zone.warehouse_id == wh.warehouse_id)).all()
        aisles = session.scalars(select(Aisle).where(Aisle.warehouse_id == wh.warehouse_id)).all()
        zone_ids = [z.zone_id for z in zones]
        locs = session.scalars(select(Location).where(Location.zone_id.in_(zone_ids))).all() if zone_ids else []

        tot_locs = len(locs)
        occ_locs = sum(
            1 for loc in locs if float(loc.current_weight_kg) > 0.0 or float(loc.current_volume_m3) > 0.0
        )
        blk_locs = sum(1 for loc in locs if bool(loc.is_blocked))
        blk_aisles = sum(1 for a in aisles if bool(a.is_blocked))

        max_wt = round(sum(float(loc.max_weight_kg) for loc in locs), 2)
        curr_wt = round(sum(float(loc.current_weight_kg) for loc in locs), 2)
        max_vol = round(sum(float(loc.max_volume_m3) for loc in locs), 4)
        curr_vol = round(sum(float(loc.current_volume_m3) for loc in locs), 4)

        return WarehouseSummaryOut(
            warehouse_id=wh.warehouse_id,
            warehouse_name=wh.warehouse_name,
            warehouse_type=wh.warehouse_type,
            city=wh.city,
            state=wh.state,
            country=wh.country,
            total_area_sqft=float(wh.total_area_sqft),
            operating_hours=wh.operating_hours,
            daily_labor_minutes_capacity=float(wh.daily_labor_minutes_capacity),
            depot_x=float(wh.depot_x),
            depot_y=float(wh.depot_y),
            status=wh.status,
            created_at=wh.created_at,
            zone_count=len(zones),
            aisle_count=len(aisles),
            blocked_aisle_count=blk_aisles,
            total_locations=tot_locs,
            occupied_locations=occ_locs,
            blocked_locations=blk_locs,
            location_utilization_pct=round((occ_locs / tot_locs) * 100.0, 2) if tot_locs > 0 else 0.0,
            total_max_weight_kg=max_wt,
            total_current_weight_kg=curr_wt,
            weight_utilization_pct=round((curr_wt / max(max_wt, 1.0)) * 100.0, 2),
            total_max_volume_m3=max_vol,
            total_current_volume_m3=curr_vol,
            volume_utilization_pct=round((curr_vol / max(max_vol, 1e-6)) * 100.0, 2),
        )
