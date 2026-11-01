"""SQLAlchemy 2.x repository for OptimizationRuns, SlotAssignments, AssignmentReasons, and Scenarios."""

from __future__ import annotations

from typing import Any

from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session

from backend.app.models import AssignmentReason, OptimizationRun, Scenario, SlotAssignment
from backend.app.models.enums import AssignmentStatus
from backend.app.repositories.base import build_pagination_meta, clamp_pagination
from backend.app.schemas.common import PaginatedResponse
from backend.app.schemas.optimization import (
    AssignmentReasonOut,
    OptimizationRunOut,
    ScenarioOut,
    SlotAssignmentOut,
)


class OptimizationRepository:
    """Data-access repository for slotting optimization runs, assignment lineage, and scenarios."""

    def list_runs(
        self,
        session: Session,
        warehouse_scope: str | None = None,
        algorithm_name: str | None = None,
        run_status: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> PaginatedResponse[OptimizationRunOut]:
        """List immutable OptimizationRun records ordered newest first."""
        safe_page, safe_size, offset = clamp_pagination(page, page_size)
        stmt = select(OptimizationRun)
        if warehouse_scope:
            stmt = stmt.where(OptimizationRun.warehouse_scope == warehouse_scope)
        if algorithm_name:
            stmt = stmt.where(OptimizationRun.algorithm_name == algorithm_name.upper())
        if run_status:
            stmt = stmt.where(OptimizationRun.run_status == run_status.upper())

        total_items = int(session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
        rows = session.scalars(
            stmt.order_by(OptimizationRun.created_at.desc(), OptimizationRun.run_id.desc())
            .offset(offset)
            .limit(safe_size)
        ).all()

        return PaginatedResponse(
            items=[OptimizationRunOut.model_validate(r) for r in rows],
            meta=build_pagination_meta(safe_page, safe_size, total_items),
        )

    @staticmethod
    def get_run(session: Session, run_id: str) -> OptimizationRun | None:
        return session.get(OptimizationRun, run_id)

    def list_assignments(
        self,
        session: Session,
        run_id: str | None = None,
        warehouse_id: str | None = None,
        sku_id: str | None = None,
        location_id: str | None = None,
        assignment_status: str | None = None,
        include_reasons: bool = False,
        sort_by: str = "assignment_score",
        sort_order: str = "desc",
        page: int = 1,
        page_size: int = 50,
    ) -> PaginatedResponse[SlotAssignmentOut]:
        """List versioned SlotAssignment records with optional candidate rationale attachment."""
        safe_page, safe_size, offset = clamp_pagination(page, page_size, max_page_size=500)
        stmt = select(SlotAssignment)
        if run_id:
            stmt = stmt.where(SlotAssignment.optimization_run_id == run_id)
        if warehouse_id:
            stmt = stmt.where(SlotAssignment.warehouse_id == warehouse_id)
        if sku_id:
            stmt = stmt.where(SlotAssignment.sku_id == sku_id)
        if location_id:
            stmt = stmt.where(SlotAssignment.location_id == location_id)
        if assignment_status:
            stmt = stmt.where(SlotAssignment.assignment_status == assignment_status.upper())

        total_items = int(session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)

        sort_map: dict[str, Any] = {
            "slot_assignment_id": SlotAssignment.slot_assignment_id,
            "sku_id": SlotAssignment.sku_id,
            "location_id": SlotAssignment.location_id,
            "assignment_score": SlotAssignment.assignment_score,
            "decision_version": SlotAssignment.decision_version,
            "created_at": SlotAssignment.created_at,
        }
        col = sort_map.get(sort_by, SlotAssignment.assignment_score)
        order_fn = desc if sort_order.lower() == "desc" else asc
        rows = session.scalars(
            stmt.order_by(order_fn(col), SlotAssignment.slot_assignment_id).offset(offset).limit(safe_size)
        ).all()

        reasons_by_run_sku: dict[tuple[str, str], list[AssignmentReasonOut]] = {}
        if include_reasons and rows:
            run_ids = sorted({r.optimization_run_id for r in rows if r.optimization_run_id is not None})
            sku_ids = sorted({r.sku_id for r in rows})
            if run_ids and sku_ids:
                r_orm = session.scalars(
                    select(AssignmentReason)
                    .where(
                        AssignmentReason.optimization_run_id.in_(run_ids),
                        AssignmentReason.sku_id.in_(sku_ids),
                    )
                    .order_by(AssignmentReason.is_selected.desc(), AssignmentReason.total_score.desc())
                ).all()
                for ar in r_orm:
                    reasons_by_run_sku.setdefault((ar.optimization_run_id, ar.sku_id), []).append(
                        AssignmentReasonOut.model_validate(ar)
                    )

        items: list[SlotAssignmentOut] = []
        for r in rows:
            out = SlotAssignmentOut.model_validate(r)
            if include_reasons and r.optimization_run_id:
                out.candidate_reasons = reasons_by_run_sku.get((r.optimization_run_id, r.sku_id), [])
            items.append(out)

        return PaginatedResponse(
            items=items,
            meta=build_pagination_meta(safe_page, safe_size, total_items),
        )

    def get_optimization_analytics_summary(self, session: Session) -> dict[str, Any]:
        """Compute historical run stats, assignment lifecycle counts, and active score component averages."""
        runs = session.scalars(
            select(OptimizationRun).order_by(OptimizationRun.created_at.desc(), OptimizationRun.run_id.desc())
        ).all()
        status_rows = session.execute(
            select(SlotAssignment.assignment_status, func.count()).group_by(SlotAssignment.assignment_status)
        ).all()
        status_counts = {str(r[0]): int(r[1]) for r in status_rows}

        score_avgs = session.execute(
            select(
                func.coalesce(func.avg(SlotAssignment.assignment_score), 0.0),
                func.coalesce(func.avg(SlotAssignment.velocity_score), 0.0),
                func.coalesce(func.avg(SlotAssignment.pick_frequency_score), 0.0),
                func.coalesce(func.avg(SlotAssignment.distance_score), 0.0),
                func.coalesce(func.avg(SlotAssignment.capacity_score), 0.0),
                func.coalesce(func.avg(SlotAssignment.replenishment_score), 0.0),
                func.coalesce(func.avg(SlotAssignment.compatibility_score), 0.0),
            ).where(SlotAssignment.assignment_status == AssignmentStatus.ACTIVE.value)
        ).one()

        avg_components = {
            "assignment_score": round(float(score_avgs[0]), 4),
            "velocity_score": round(float(score_avgs[1]), 4),
            "pick_frequency_score": round(float(score_avgs[2]), 4),
            "distance_score": round(float(score_avgs[3]), 4),
            "capacity_score": round(float(score_avgs[4]), 4),
            "replenishment_score": round(float(score_avgs[5]), 4),
            "compatibility_score": round(float(score_avgs[6]), 4),
        }

        run_dicts = [OptimizationRunOut.model_validate(r).model_dump(mode="json") for r in runs]
        return {
            "total_runs": len(runs),
            "latest_run": run_dicts[0] if run_dicts else None,
            "assignment_status_counts": status_counts,
            "average_active_score_components": avg_components,
            "run_history": run_dicts[:25],
        }

    def list_scenarios(
        self,
        session: Session,
        warehouse_id: str | None = None,
        optimization_run_id: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> PaginatedResponse[ScenarioOut]:
        """List persisted Scenario comparison records."""
        safe_page, safe_size, offset = clamp_pagination(page, page_size)
        stmt = select(Scenario)
        if warehouse_id:
            stmt = stmt.where(Scenario.warehouse_id == warehouse_id)
        if optimization_run_id:
            stmt = stmt.where(Scenario.optimization_run_id == optimization_run_id)

        total_items = int(session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
        rows = session.scalars(
            stmt.order_by(Scenario.created_at.desc(), Scenario.scenario_id.desc()).offset(offset).limit(safe_size)
        ).all()

        return PaginatedResponse(
            items=[ScenarioOut.model_validate(s) for s in rows],
            meta=build_pagination_meta(safe_page, safe_size, total_items),
        )

    @staticmethod
    def get_scenario(session: Session, scenario_id: str) -> Scenario | None:
        return session.get(Scenario, scenario_id)
