"""Scenario comparison service contrasting baseline slotting against an optimization run."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.config import DomainConfig, load_domain_config
from backend.app.models import Location, OptimizationRun, Scenario, SlotAssignment, Zone
from backend.app.services.audit_service import AuditService, utc_now_naive
from backend.app.simulation.picking_simulator import PickingSimulationService
from backend.app.simulation.replenishment_simulator import ReplenishmentSimulationService
from backend.app.utils.ids import extract_trailing_sequence, format_scenario_id


class ScenarioComparisonService:
    """Evaluates and persists measurable deltas between legacy baseline slotting and an OptimizationRun."""

    def __init__(self, config: DomainConfig | None = None) -> None:
        self.config = config or load_domain_config()
        self.picking_sim = PickingSimulationService(self.config)
        self.rep_sim = ReplenishmentSimulationService(self.config)
        self.audit_service = AuditService(reference_year=self.config.dataset.reference_year)

    def create_and_evaluate_scenario(
        self,
        session: Session,
        optimization_run_id: str,
        scenario_name: str | None = None,
        description: str = "",
        warehouse_id: str | None = None,
        max_orders: int = 500,
        actor: str = "SCENARIO_ENGINE",
    ) -> Scenario:
        """Compare baseline slotting vs. `optimization_run_id` across picking, capacity, replenishment, and violations."""
        opt_run = session.get(OptimizationRun, optimization_run_id)
        if opt_run is None:
            raise KeyError(f"OptimizationRun not found: {optimization_run_id}")

        eff_wh = warehouse_id or (None if opt_run.warehouse_scope == "ALL" else opt_run.warehouse_scope)

        # 1. Picking distance & time comparison on identical orders
        pick_cmp = self.picking_sim.compare_slotting_layouts(
            session=session,
            optimization_run_id=optimization_run_id,
            warehouse_id=eff_wh,
            max_orders=max_orders,
        )

        # 2. Replenishment workload comparison
        base_rep = self.rep_sim.simulate_replenishment(
            session=session,
            warehouse_id=eff_wh,
            use_baseline_assignments=True,
            persist_events=False,
        )
        opt_rep = self.rep_sim.simulate_replenishment(
            session=session,
            warehouse_id=eff_wh,
            optimization_run_id=optimization_run_id,
            persist_events=False,
        )

        # 3. Assignment quality, utilization, and violation counts
        zone_wh = {z.zone_id: z.warehouse_id for z in session.scalars(select(Zone)).all()}
        locs = {loc.location_id: loc for loc in session.scalars(select(Location)).all()}
        scoped_locs = [loc for loc in locs.values() if eff_wh is None or zone_wh.get(loc.zone_id) == eff_wh]
        total_loc_count = max(1, len(scoped_locs))
        total_max_vol = sum(float(loc.max_volume_m3) for loc in scoped_locs) or 1.0
        total_max_wt = sum(float(loc.max_weight_kg) for loc in scoped_locs) or 1.0

        base_stmt = select(SlotAssignment).where(SlotAssignment.optimization_run_id.is_(None))
        opt_stmt = select(SlotAssignment).where(SlotAssignment.optimization_run_id == optimization_run_id)
        if eff_wh:
            base_stmt = base_stmt.where(SlotAssignment.warehouse_id == eff_wh)
            opt_stmt = opt_stmt.where(SlotAssignment.warehouse_id == eff_wh)

        base_assns = session.scalars(base_stmt).all()
        opt_assns = session.scalars(opt_stmt).all()

        def _layout_stats(rows: list[SlotAssignment]) -> dict[str, Any]:
            if not rows:
                return {
                    "assignment_count": 0,
                    "occupied_locations": 0,
                    "location_occupancy_pct": 0.0,
                    "weight_utilization_pct": 0.0,
                    "volume_utilization_pct": 0.0,
                    "average_assignment_score": 0.0,
                    "capacity_violation_locations": 0,
                }
            by_loc_wt: dict[str, float] = {}
            by_loc_vol: dict[str, float] = {}
            for r in rows:
                by_loc_wt[r.location_id] = by_loc_wt.get(r.location_id, 0.0) + float(r.allocated_weight_kg)
                by_loc_vol[r.location_id] = by_loc_vol.get(r.location_id, 0.0) + float(r.allocated_volume_m3)

            cap_viols = sum(
                1
                for lid in by_loc_wt
                if lid in locs
                and (
                    by_loc_wt[lid] > float(locs[lid].max_weight_kg) + 1e-4
                    or by_loc_vol[lid] > float(locs[lid].max_volume_m3) + 1e-6
                )
            )
            avg_sc = sum(float(r.assignment_score) for r in rows) / len(rows)
            return {
                "assignment_count": len(rows),
                "occupied_locations": len(by_loc_wt),
                "location_occupancy_pct": round((len(by_loc_wt) / total_loc_count) * 100.0, 2),
                "weight_utilization_pct": round((sum(by_loc_wt.values()) / total_max_wt) * 100.0, 2),
                "volume_utilization_pct": round((sum(by_loc_vol.values()) / total_max_vol) * 100.0, 2),
                "average_assignment_score": round(avg_sc, 4),
                "capacity_violation_locations": cap_viols,
            }

        base_stats = _layout_stats(base_assns)
        opt_stats = _layout_stats(opt_assns)

        baseline_metrics = {
            **base_stats,
            "pick_distance_m": pick_cmp["baseline_distance_m"],
            "pick_time_seconds": pick_cmp["baseline_time_seconds"],
            "replenishment_requests": base_rep["total_requests"],
            "replenishment_labor_minutes": base_rep["total_labor_minutes_required"],
            "replenishment_overloaded_tasks": base_rep["overloaded_task_count"],
        }
        optimized_metrics = {
            **opt_stats,
            "pick_distance_m": pick_cmp["optimized_distance_m"],
            "pick_time_seconds": pick_cmp["optimized_time_seconds"],
            "replenishment_requests": opt_rep["total_requests"],
            "replenishment_labor_minutes": opt_rep["total_labor_minutes_required"],
            "replenishment_overloaded_tasks": opt_rep["overloaded_task_count"],
        }
        comparison_delta = {
            "orders_compared": pick_cmp["orders_evaluated"],
            "order_lines_compared": pick_cmp["order_lines_evaluated"],
            "pick_distance_saved_m": pick_cmp["distance_saved_m"],
            "pick_distance_reduction_pct": pick_cmp["percentage_improvement"],
            "pure_slotting_distance_saved_m": pick_cmp["pure_slotting_distance_saved_m"],
            "pick_time_saved_seconds": pick_cmp["estimated_time_saved_seconds"],
            "pick_time_improvement_pct": pick_cmp["time_improvement_pct"],
            "average_score_delta": round(
                opt_stats["average_assignment_score"] - base_stats["average_assignment_score"], 4
            ),
            "capacity_violations_eliminated": (
                base_stats["capacity_violation_locations"] - opt_stats["capacity_violation_locations"]
            ),
            "weight_utilization_delta_pct": round(
                opt_stats["weight_utilization_pct"] - base_stats["weight_utilization_pct"], 2
            ),
            "replenishment_labor_delta_minutes": round(
                opt_rep["total_labor_minutes_required"] - base_rep["total_labor_minutes_required"], 2
            ),
        }

        max_scn_id = session.scalar(select(func.max(Scenario.scenario_id)))
        scn_id = format_scenario_id(self.config.dataset.reference_year, extract_trailing_sequence(max_scn_id) + 1)
        now_dt = utc_now_naive()
        name = scenario_name or f"Baseline vs {optimization_run_id} ({eff_wh or 'All Warehouses'})"

        scenario = Scenario(
            scenario_id=scn_id,
            scenario_name=name,
            description=description or "Automated operational comparison across identical fulfillment orders.",
            warehouse_id=eff_wh,
            optimization_run_id=optimization_run_id,
            parameters_json={"warehouse_scope": eff_wh or "ALL", "max_orders": max_orders},
            baseline_metrics_json=baseline_metrics,
            optimized_metrics_json=optimized_metrics,
            comparison_delta_json=comparison_delta,
            created_at=now_dt,
        )
        session.add(scenario)
        self.audit_service.log_event(
            session=session,
            event_type="SCENARIO_COMPARISON_CREATED",
            entity_type="SCENARIO",
            entity_id=scn_id,
            actor=actor,
            previous_state=None,
            new_state={"optimization_run_id": optimization_run_id, "delta": comparison_delta},
            timestamp=now_dt,
        )
        session.flush()
        return scenario
