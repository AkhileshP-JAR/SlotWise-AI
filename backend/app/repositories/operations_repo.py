"""SQLAlchemy 2.x repository for Picking telemetry, Replenishment queues, Exceptions, and System Audit counts."""

from __future__ import annotations

from typing import Any

from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session

from backend.app.models import (
    SKU,
    Aisle,
    AssignmentReason,
    AuditEvent,
    CompatibilityRule,
    ExceptionRecord,
    HazardClass,
    Location,
    OptimizationRun,
    Order,
    OrderLine,
    Picker,
    PickEvent,
    Replenishment,
    Scenario,
    SKUProfile,
    SlotAssignment,
    Supplier,
    Warehouse,
    Zone,
    ZoneHazardAllowance,
)
from backend.app.models.enums import ReplenishmentStatus
from backend.app.repositories.base import build_pagination_meta, clamp_pagination
from backend.app.schemas.common import AuditEventOut, PaginatedResponse
from backend.app.schemas.operations import (
    ExceptionOut,
    OrderBriefOut,
    ReplenishmentOut,
    ReplenishmentWarehouseSummary,
)


class OperationsRepository:
    """Data-access repository for operational picking, replenishment, exception, and audit queries."""

    def get_picking_telemetry_and_orders(
        self,
        session: Session,
        warehouse_id: str | None = None,
        recent_limit: int = 40,
    ) -> tuple[dict[str, Any], list[OrderBriefOut]]:
        """Query observed PickEvent telemetry and recent outbound orders for route inspection."""
        pe_stmt = select(
            func.count(PickEvent.pick_event_id),
            func.coalesce(func.sum(PickEvent.quantity_picked), 0),
            func.coalesce(func.sum(PickEvent.travel_distance_m), 0.0),
            func.coalesce(func.avg(PickEvent.travel_distance_m), 0.0),
            func.coalesce(func.avg(PickEvent.pick_duration_seconds), 0.0),
        ).join(Order, PickEvent.order_id == Order.order_id)
        if warehouse_id:
            pe_stmt = pe_stmt.where(Order.warehouse_id == warehouse_id)

        pe_stats = session.execute(pe_stmt).one()

        status_stmt = (
            select(PickEvent.event_status, func.count())
            .join(Order, PickEvent.order_id == Order.order_id)
            .group_by(PickEvent.event_status)
        )
        if warehouse_id:
            status_stmt = status_stmt.where(Order.warehouse_id == warehouse_id)
        status_counts = {str(r[0]): int(r[1]) for r in session.execute(status_stmt).all()}

        observed = {
            "total_pick_events": int(pe_stats[0] or 0),
            "total_units_picked": int(pe_stats[1] or 0),
            "observed_total_travel_distance_m": round(float(pe_stats[2] or 0.0), 2),
            "observed_avg_distance_per_pick_m": round(float(pe_stats[3] or 0.0), 2),
            "observed_avg_duration_seconds": round(float(pe_stats[4] or 0.0), 2),
            "event_status_breakdown": status_counts,
        }

        ord_stmt = (
            select(
                Order.order_id,
                Order.warehouse_id,
                Order.order_date,
                Order.order_priority,
                Order.order_status,
                Order.customer_region,
                func.count(OrderLine.order_line_id).label("line_cnt"),
            )
            .join(OrderLine, Order.order_id == OrderLine.order_id)
            .group_by(
                Order.order_id,
                Order.warehouse_id,
                Order.order_date,
                Order.order_priority,
                Order.order_status,
                Order.customer_region,
            )
            .order_by(Order.order_date.desc(), Order.order_id.asc())
            .limit(recent_limit)
        )
        if warehouse_id:
            ord_stmt = ord_stmt.where(Order.warehouse_id == warehouse_id)

        recent_orders = [
            OrderBriefOut(
                order_id=str(r[0]),
                warehouse_id=str(r[1]),
                order_date=r[2],
                order_priority=str(r[3]),
                order_status=str(r[4]),
                customer_region=str(r[5]),
                line_count=int(r[6]),
            )
            for r in session.execute(ord_stmt).all()
        ]
        return observed, recent_orders

    def get_replenishment_analytics_data(
        self,
        session: Session,
        warehouse_id: str | None = None,
        priority: str | None = None,
        replenishment_status: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """Aggregate persisted Replenishment queue metrics and return a paginated queue slice."""
        safe_page, safe_size, offset = clamp_pagination(page, page_size, max_page_size=250)

        wh_stmt = select(Warehouse).order_by(Warehouse.warehouse_id)
        if warehouse_id:
            wh_stmt = wh_stmt.where(Warehouse.warehouse_id == warehouse_id)
        warehouses = session.scalars(wh_stmt).all()

        all_stmt = select(Replenishment)
        if warehouse_id:
            all_stmt = all_stmt.where(Replenishment.warehouse_id == warehouse_id)
        all_reps = session.scalars(all_stmt).all()

        prio_dist: dict[str, int] = {}
        stat_dist: dict[str, int] = {}
        active_by_wh: dict[str, list[Replenishment]] = {w.warehouse_id: [] for w in warehouses}

        active_statuses = {
            ReplenishmentStatus.PENDING.value,
            ReplenishmentStatus.IN_PROGRESS.value,
            ReplenishmentStatus.OVERLOADED.value,
        }
        active_cnt = 0
        overloaded_cnt = 0
        for rep in all_reps:
            prio_dist[rep.priority] = prio_dist.get(rep.priority, 0) + 1
            stat_dist[rep.replenishment_status] = stat_dist.get(rep.replenishment_status, 0) + 1
            if rep.replenishment_status in active_statuses:
                active_cnt += 1
                active_by_wh.setdefault(rep.warehouse_id, []).append(rep)
            if rep.replenishment_status == ReplenishmentStatus.OVERLOADED.value:
                overloaded_cnt += 1

        wh_summaries: list[ReplenishmentWarehouseSummary] = []
        for wh in warehouses:
            w_reps = active_by_wh.get(wh.warehouse_id, [])
            req_m = round(sum(float(r.labor_minutes_required) for r in w_reps), 2)
            assn_m = round(sum(float(r.assigned_labor_minutes) for r in w_reps), 2)
            cap_m = round(float(wh.daily_labor_minutes_capacity), 2)
            ov_cnt = sum(1 for r in w_reps if r.replenishment_status == ReplenishmentStatus.OVERLOADED.value)
            util = round((req_m / cap_m) * 100.0, 2) if cap_m > 0 else 0.0
            wh_summaries.append(
                ReplenishmentWarehouseSummary(
                    warehouse_id=wh.warehouse_id,
                    task_count=len(w_reps),
                    overloaded_task_count=ov_cnt,
                    labor_minutes_required=req_m,
                    available_labor_minutes=cap_m,
                    assigned_labor_minutes=assn_m,
                    labor_utilization_pct=util,
                    is_overloaded=req_m > cap_m + 1e-3 or ov_cnt > 0,
                )
            )

        q_stmt = select(Replenishment)
        if warehouse_id:
            q_stmt = q_stmt.where(Replenishment.warehouse_id == warehouse_id)
        if priority:
            q_stmt = q_stmt.where(Replenishment.priority == priority.upper())
        if replenishment_status:
            q_stmt = q_stmt.where(Replenishment.replenishment_status == replenishment_status.upper())

        total_q = int(session.scalar(select(func.count()).select_from(q_stmt.subquery())) or 0)
        q_rows = session.scalars(
            q_stmt.order_by(Replenishment.labor_minutes_required.desc(), Replenishment.replenishment_id.asc())
            .offset(offset)
            .limit(safe_size)
        ).all()

        return {
            "warehouse_scope": warehouse_id or "ALL",
            "total_tasks": len(all_reps),
            "active_tasks": active_cnt,
            "overloaded_tasks": overloaded_cnt,
            "priority_distribution": dict(sorted(prio_dist.items())),
            "status_distribution": dict(sorted(stat_dist.items())),
            "warehouse_labor_summary": wh_summaries,
            "queue_page": [ReplenishmentOut.model_validate(r) for r in q_rows],
            "total_queue_items": total_q,
            "page": safe_page,
            "page_size": safe_size,
        }

    def list_exceptions(
        self,
        session: Session,
        category: str | None = None,
        issue_type: str | None = None,
        severity: str | None = None,
        resolution_status: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        optimization_run_id: str | None = None,
        ground_truth_pairs: set[tuple[str, str]] | None = None,
        sort_by: str = "exception_id",
        sort_order: str = "asc",
        page: int = 1,
        page_size: int = 50,
    ) -> PaginatedResponse[ExceptionOut]:
        """List operational ExceptionRecords with optional read-only ground-truth presentation flag."""
        safe_page, safe_size, offset = clamp_pagination(page, page_size, max_page_size=250)
        stmt = select(ExceptionRecord)
        if category:
            stmt = stmt.where(ExceptionRecord.category == category.upper())
        if issue_type:
            stmt = stmt.where(ExceptionRecord.issue_type == issue_type.upper())
        if severity:
            stmt = stmt.where(ExceptionRecord.severity == severity.upper())
        if resolution_status:
            stmt = stmt.where(ExceptionRecord.resolution_status == resolution_status.upper())
        if entity_type:
            stmt = stmt.where(ExceptionRecord.entity_type == entity_type.upper())
        if entity_id:
            stmt = stmt.where(ExceptionRecord.entity_id == entity_id)
        if optimization_run_id:
            stmt = stmt.where(ExceptionRecord.optimization_run_id == optimization_run_id)

        total_items = int(session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)

        sort_map: dict[str, Any] = {
            "exception_id": ExceptionRecord.exception_id,
            "severity": ExceptionRecord.severity,
            "issue_type": ExceptionRecord.issue_type,
            "detected_at": ExceptionRecord.detected_at,
            "resolution_status": ExceptionRecord.resolution_status,
        }
        col = sort_map.get(sort_by, ExceptionRecord.exception_id)
        order_fn = desc if sort_order.lower() == "desc" else asc
        rows = session.scalars(
            stmt.order_by(order_fn(col), ExceptionRecord.exception_id).offset(offset).limit(safe_size)
        ).all()

        items: list[ExceptionOut] = []
        for e in rows:
            out = ExceptionOut.model_validate(e)
            if ground_truth_pairs is not None:
                out.ground_truth_flag = (e.issue_type, e.entity_id) in ground_truth_pairs
            items.append(out)

        return PaginatedResponse(
            items=items,
            meta=build_pagination_meta(safe_page, safe_size, total_items),
        )

    @staticmethod
    def get_exception(session: Session, exception_id: str) -> ExceptionRecord | None:
        return session.get(ExceptionRecord, exception_id)

    def list_audit_events(
        self,
        session: Session,
        entity_type: str | None = None,
        entity_id: str | None = None,
        event_type: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> PaginatedResponse[AuditEventOut]:
        """List immutable AuditEvent records ordered newest first."""
        safe_page, safe_size, offset = clamp_pagination(page, page_size, max_page_size=250)
        stmt = select(AuditEvent)
        if entity_type:
            stmt = stmt.where(AuditEvent.entity_type == entity_type.upper())
        if entity_id:
            stmt = stmt.where(AuditEvent.entity_id == entity_id)
        if event_type:
            stmt = stmt.where(AuditEvent.event_type == event_type.upper())

        total_items = int(session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
        rows = session.scalars(
            stmt.order_by(AuditEvent.created_at.desc(), AuditEvent.audit_event_id.desc())
            .offset(offset)
            .limit(safe_size)
        ).all()

        return PaginatedResponse(
            items=[AuditEventOut.model_validate(r) for r in rows],
            meta=build_pagination_meta(safe_page, safe_size, total_items),
        )

    @staticmethod
    def get_all_table_counts(session: Session) -> dict[str, int]:
        """Return exact row counts across all 21 relational tables."""
        models_map: dict[str, Any] = {
            "warehouses": Warehouse,
            "hazard_classes": HazardClass,
            "zones": Zone,
            "zone_hazard_allowances": ZoneHazardAllowance,
            "compatibility_rules": CompatibilityRule,
            "aisles": Aisle,
            "locations": Location,
            "suppliers": Supplier,
            "skus": SKU,
            "sku_profiles": SKUProfile,
            "orders": Order,
            "order_lines": OrderLine,
            "pickers": Picker,
            "pick_events": PickEvent,
            "replenishments": Replenishment,
            "optimization_runs": OptimizationRun,
            "slot_assignments": SlotAssignment,
            "assignment_reasons": AssignmentReason,
            "scenarios": Scenario,
            "exceptions": ExceptionRecord,
            "audit_events": AuditEvent,
        }
        return {
            name: int(session.scalar(select(func.count()).select_from(model_cls)) or 0)
            for name, model_cls in models_map.items()
        }
