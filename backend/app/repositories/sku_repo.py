"""SQLAlchemy 2.x repository for SKUs, analytical SKUProfiles, demand history, and assignments."""

from __future__ import annotations

from typing import Any

from sqlalchemy import asc, desc, func, or_, select
from sqlalchemy.orm import Session

from backend.app.models import SKU, Order, OrderLine, SKUProfile, SlotAssignment, Supplier
from backend.app.models.enums import AssignmentStatus
from backend.app.repositories.base import build_pagination_meta, clamp_pagination
from backend.app.schemas.common import PaginatedResponse
from backend.app.schemas.sku import (
    DailyDemandPoint,
    SKUAssignmentBrief,
    SKUDetailOut,
    SKUOut,
    SKUProfileOut,
)


class SKURepository:
    """Data-access repository for SKU master data, profiles, demand trends, and slot history."""

    def list_skus(
        self,
        session: Session,
        search: str | None = None,
        category: str | None = None,
        hazard_class: str | None = None,
        temperature_requirement: str | None = None,
        demand_velocity: str | None = None,
        abc_class: str | None = None,
        xyz_class: str | None = None,
        warehouse_id: str | None = None,
        status: str | None = None,
        sort_by: str = "sku_id",
        sort_order: str = "asc",
        page: int = 1,
        page_size: int = 25,
    ) -> PaginatedResponse[SKUOut]:
        """List SKUs joined with active SKUProfile attributes and current active slot assignment."""
        safe_page, safe_size, offset = clamp_pagination(page, page_size, max_page_size=250)

        stmt = select(SKU, SKUProfile).outerjoin(
            SKUProfile,
            (SKU.sku_id == SKUProfile.sku_id) & (SKUProfile.effective_to.is_(None)),
        )

        if search:
            q = f"%{search.strip()}%"
            stmt = stmt.where(or_(SKU.sku_id.ilike(q), SKU.sku_name.ilike(q), SKU.category.ilike(q)))
        if category:
            stmt = stmt.where(SKU.category == category.upper())
        if hazard_class:
            stmt = stmt.where(SKU.hazard_class == hazard_class.upper())
        if temperature_requirement:
            stmt = stmt.where(SKU.temperature_requirement == temperature_requirement.upper())
        if demand_velocity:
            stmt = stmt.where(SKUProfile.demand_velocity == demand_velocity.upper())
        if abc_class:
            stmt = stmt.where(SKUProfile.abc_class == abc_class.upper())
        if xyz_class:
            stmt = stmt.where(SKUProfile.xyz_class == xyz_class.upper())
        if status:
            stmt = stmt.where(SKU.status == status.upper())
        if warehouse_id:
            wh_sku_subq = (
                select(SlotAssignment.sku_id)
                .where(
                    SlotAssignment.warehouse_id == warehouse_id,
                    SlotAssignment.assignment_status == AssignmentStatus.ACTIVE.value,
                )
                .distinct()
            )
            stmt = stmt.where(SKU.sku_id.in_(wh_sku_subq))

        total_items = int(session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)

        sort_map: dict[str, Any] = {
            "sku_id": SKU.sku_id,
            "sku_name": SKU.sku_name,
            "category": SKU.category,
            "unit_price": SKU.unit_price,
            "unit_weight_kg": SKU.unit_weight_kg,
            "average_daily_demand": SKUProfile.average_daily_demand,
            "pick_frequency": SKUProfile.pick_frequency,
            "velocity_percentile": SKUProfile.velocity_percentile,
            "reorder_point": SKUProfile.reorder_point,
        }
        col = sort_map.get(sort_by, SKU.sku_id)
        order_fn = desc if sort_order.lower() == "desc" else asc
        rows = session.execute(stmt.order_by(order_fn(col), SKU.sku_id).offset(offset).limit(safe_size)).all()

        sku_ids = [r[0].sku_id for r in rows]
        active_assn_by_sku: dict[str, SlotAssignment] = {}
        if sku_ids:
            for assn in session.scalars(
                select(SlotAssignment)
                .where(
                    SlotAssignment.sku_id.in_(sku_ids),
                    SlotAssignment.assignment_status == AssignmentStatus.ACTIVE.value,
                )
                .order_by(SlotAssignment.slot_assignment_id)
            ).all():
                active_assn_by_sku.setdefault(assn.sku_id, assn)

        items = [self._to_sku_out(sku, prof, active_assn_by_sku.get(sku.sku_id)) for sku, prof in rows]
        return PaginatedResponse(
            items=items,
            meta=build_pagination_meta(safe_page, safe_size, total_items),
        )

    def get_sku_detail(self, session: Session, sku_id: str) -> SKUDetailOut | None:
        """Retrieve complete SKU master record, supplier, active profile, demand trend, and assignment lineage."""
        sku = session.get(SKU, sku_id)
        if sku is None:
            return None

        prof = self.get_active_profile(session, sku_id)
        supplier = session.get(Supplier, sku.supplier_id) if sku.supplier_id else None

        assns = session.scalars(
            select(SlotAssignment)
            .where(SlotAssignment.sku_id == sku_id)
            .order_by(SlotAssignment.decision_version.desc(), SlotAssignment.slot_assignment_id.desc())
        ).all()
        active_assns = [a for a in assns if a.assignment_status == AssignmentStatus.ACTIVE.value]
        primary_active = active_assns[0] if active_assns else None

        # Aggregate daily order-line demand time series
        day_expr = func.date(Order.order_date)
        trend_rows = session.execute(
            select(
                day_expr.label("day_str"),
                func.sum(OrderLine.quantity),
                func.count(OrderLine.order_line_id),
                func.sum(OrderLine.line_value),
            )
            .join(Order, OrderLine.order_id == Order.order_id)
            .where(OrderLine.sku_id == sku_id)
            .group_by(day_expr)
            .order_by(day_expr)
        ).all()

        trend = [
            DailyDemandPoint(
                order_date=str(r[0]),
                units_ordered=int(r[1] or 0),
                order_lines=int(r[2] or 0),
                revenue=round(float(r[3] or 0.0), 2),
            )
            for r in trend_rows
        ]

        base = self._to_sku_out(sku, prof, primary_active)
        return SKUDetailOut(
            **base.model_dump(),
            supplier_name=supplier.supplier_name if supplier else None,
            supplier_lead_time_days=float(supplier.average_lead_time_days) if supplier else None,
            profile=SKUProfileOut.model_validate(prof) if prof else None,
            active_assignments=[SKUAssignmentBrief.model_validate(a) for a in active_assns],
            assignment_history=[SKUAssignmentBrief.model_validate(a) for a in assns],
            demand_trend=trend,
        )

    @staticmethod
    def get_active_profile(session: Session, sku_id: str) -> SKUProfile | None:
        """Retrieve the currently effective analytical SKUProfile for a SKU."""
        return session.scalars(
            select(SKUProfile)
            .where(SKUProfile.sku_id == sku_id, SKUProfile.effective_to.is_(None))
            .order_by(SKUProfile.profile_id.desc())
            .limit(1)
        ).first()

    @staticmethod
    def _to_sku_out(sku: SKU, prof: SKUProfile | None, active_assn: SlotAssignment | None) -> SKUOut:
        return SKUOut(
            sku_id=sku.sku_id,
            sku_name=sku.sku_name,
            category=sku.category,
            unit_price=float(sku.unit_price),
            unit_weight_kg=float(sku.unit_weight_kg),
            width_cm=float(sku.width_cm),
            height_cm=float(sku.height_cm),
            depth_cm=float(sku.depth_cm),
            unit_volume_m3=round(float(sku.unit_volume_m3), 6),
            is_fragile=bool(sku.is_fragile),
            hazard_class=sku.hazard_class,
            temperature_requirement=sku.temperature_requirement,
            storage_type=sku.storage_type,
            supplier_id=sku.supplier_id,
            status=sku.status,
            demand_velocity=prof.demand_velocity if prof else None,
            abc_class=prof.abc_class if prof else None,
            xyz_class=prof.xyz_class if prof else None,
            average_daily_demand=round(float(prof.average_daily_demand), 4) if prof else None,
            pick_frequency=round(float(prof.pick_frequency), 4) if prof else None,
            current_on_hand_qty=int(prof.current_on_hand_qty) if prof else None,
            reorder_point=round(float(prof.reorder_point), 2) if prof else None,
            active_location_id=active_assn.location_id if active_assn else None,
            active_warehouse_id=active_assn.warehouse_id if active_assn else None,
        )
