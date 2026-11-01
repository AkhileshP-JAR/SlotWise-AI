"""Replenishment requirement and warehouse labor capacity simulation service (Section 8.8)."""

from __future__ import annotations

import math
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.config import DomainConfig, load_domain_config
from backend.app.models import (
    Location,
    Replenishment,
    SKUProfile,
    SlotAssignment,
    Warehouse,
    Zone,
)
from backend.app.models.enums import (
    AssignmentStatus,
    DemandVelocityClass,
    ReplenishmentPriority,
    ReplenishmentStatus,
    ZoneType,
)
from backend.app.services.audit_service import AuditService, utc_now_naive
from backend.app.utils.ids import extract_trailing_sequence, format_replenishment_id


class ReplenishmentSimulationService:
    """Simulates forward-pick replenishment triggers, labor workload, and capacity overloads."""

    def __init__(self, config: DomainConfig | None = None) -> None:
        self.config = config or load_domain_config()
        self.audit_service = AuditService(reference_year=self.config.dataset.reference_year)

    def simulate_replenishment(
        self,
        session: Session,
        warehouse_id: str | None = None,
        optimization_run_id: str | None = None,
        use_baseline_assignments: bool = False,
        labor_capacity_multiplier: float = 1.0,
        use_ml_forecasts: bool = False,
        ml_forecaster: Any | None = None,
        persist_events: bool = False,
        actor: str = "REPLENISHMENT_SIMULATOR",
    ) -> dict[str, Any]:
        """Evaluate reorder-point triggers and labor utilization across warehouses."""
        if labor_capacity_multiplier <= 0:
            raise ValueError("labor_capacity_multiplier must be > 0")

        forecast_lookup: dict[str, dict[str, Any]] | None = None
        if use_ml_forecasts:
            if ml_forecaster is None:
                from backend.app.ml.forecaster import DemandForecastingService

                ml_forecaster = DemandForecastingService(config=self.config)
            forecast_lookup = ml_forecaster.get_forecast_lookup(
                session=session,
                auto_train_if_missing=True,
            )

        wh_stmt = select(Warehouse).order_by(Warehouse.warehouse_id)
        if warehouse_id:
            wh_stmt = wh_stmt.where(Warehouse.warehouse_id == warehouse_id)
        warehouses = {w.warehouse_id: w for w in session.scalars(wh_stmt).all()}

        zones = {z.zone_id: z for z in session.scalars(select(Zone)).all()}
        locations = {loc.location_id: loc for loc in session.scalars(select(Location)).all()}
        profiles = {
            p.sku_id: p for p in session.scalars(select(SKUProfile).where(SKUProfile.effective_to.is_(None))).all()
        }

        # Map each warehouse to its reserve/oversize source bays
        reserve_locs_by_wh: dict[str, list[Location]] = {}
        for loc in locations.values():
            z = zones.get(loc.zone_id)
            if z and z.zone_type == ZoneType.OVERSIZE.value and not loc.is_blocked:
                reserve_locs_by_wh.setdefault(z.warehouse_id, []).append(loc)

        # Select target assignment snapshot
        if use_baseline_assignments:
            assn_stmt = select(SlotAssignment).where(SlotAssignment.optimization_run_id.is_(None))
        elif optimization_run_id:
            assn_stmt = select(SlotAssignment).where(SlotAssignment.optimization_run_id == optimization_run_id)
        else:
            assn_stmt = select(SlotAssignment).where(
                SlotAssignment.assignment_status == AssignmentStatus.ACTIVE.value
            )

        if warehouse_id:
            assn_stmt = assn_stmt.where(SlotAssignment.warehouse_id == warehouse_id)
        assignments = session.scalars(assn_stmt.order_by(SlotAssignment.slot_assignment_id)).all()

        rep_cfg = self.config.simulation.replenishment
        raw_tasks_by_wh: dict[str, list[dict[str, Any]]] = {w: [] for w in warehouses}
        forecast_adjusted_skus = 0

        for idx, assn in enumerate(assignments):
            wid = assn.warehouse_id
            if wid not in warehouses:
                continue
            prof = profiles.get(assn.sku_id)
            if prof is None:
                continue

            f_item = forecast_lookup.get(assn.sku_id) if forecast_lookup else None
            if f_item is not None:
                rop = float(f_item["forecast_reorder_point"])
                eff_adv = float(f_item["forecast_daily_demand"])
                eff_vel = str(f_item["forecast_velocity"])
                forecast_adjusted_skus += 1
            else:
                rop = float(prof.reorder_point)
                eff_adv = float(prof.average_daily_demand)
                eff_vel = str(prof.demand_velocity)

            curr_qty = int(assn.assigned_quantity)
            if rop <= 0.0 or curr_qty >= rop:
                continue

            dest_loc = locations.get(assn.location_id)
            if dest_loc is None:
                continue

            res_candidates = reserve_locs_by_wh.get(wid, [])
            src_loc = res_candidates[idx % len(res_candidates)] if res_candidates else None
            if src_loc and src_loc.location_id == dest_loc.location_id:
                src_loc = None

            target_qty = max(
                int(math.ceil(rop * 1.35)),
                int(math.ceil(eff_adv * int(prof.target_days_of_supply))),
                curr_qty + 5,
            )
            req_qty = max(1, target_qty - curr_qty)

            if src_loc is not None:
                travel_m = abs(float(dest_loc.x_coordinate) - float(src_loc.x_coordinate)) + abs(
                    float(dest_loc.y_coordinate) - float(src_loc.y_coordinate)
                )
                src_id = src_loc.location_id
            else:
                travel_m = 35.0
                src_id = None

            labor_req = round(
                rep_cfg.base_setup_minutes
                + (travel_m / rep_cfg.travel_speed_mps) / 60.0
                + (req_qty / 100.0) * rep_cfg.handling_minutes_per_100_units,
                3,
            )

            stockout_ratio = curr_qty / max(rop, 1e-4)
            if eff_vel == DemandVelocityClass.FAST.value and stockout_ratio < 0.55:
                prio = ReplenishmentPriority.CRITICAL.value
                prio_rank = 0
            elif eff_vel == DemandVelocityClass.FAST.value or stockout_ratio < 0.45:
                prio = ReplenishmentPriority.HIGH.value
                prio_rank = 1
            elif eff_vel == DemandVelocityClass.MEDIUM.value:
                prio = ReplenishmentPriority.NORMAL.value
                prio_rank = 2
            else:
                prio = ReplenishmentPriority.LOW.value
                prio_rank = 3

            raw_tasks_by_wh[wid].append(
                {
                    "warehouse_id": wid,
                    "sku_id": assn.sku_id,
                    "source_location_id": src_id,
                    "destination_location_id": dest_loc.location_id,
                    "current_assigned_qty": curr_qty,
                    "reorder_point": round(rop, 2),
                    "required_quantity": req_qty,
                    "priority": prio,
                    "_prio_rank": prio_rank,
                    "labor_minutes_required": labor_req,
                }
            )

        # Allocate warehouse labor budget in priority order and detect workload overloads
        warehouse_summaries: list[dict[str, Any]] = []
        simulated_tasks: list[dict[str, Any]] = []
        total_req_labor = 0.0
        total_avail_labor = 0.0
        total_overloaded_tasks = 0
        overloaded_warehouses: list[str] = []

        max_rep_id = session.scalar(select(func.max(Replenishment.replenishment_id)))
        next_rep_seq = extract_trailing_sequence(max_rep_id) + 1
        now_dt = utc_now_naive()
        year = self.config.dataset.reference_year

        for wid, wh in sorted(warehouses.items()):
            avail_cap = round(float(wh.daily_labor_minutes_capacity) * labor_capacity_multiplier, 2)
            total_avail_labor += avail_cap
            tasks = sorted(
                raw_tasks_by_wh[wid], key=lambda t: (t["_prio_rank"], -t["labor_minutes_required"], t["sku_id"])
            )

            rem_labor = avail_cap
            wh_req_labor = 0.0
            wh_assigned_labor = 0.0
            wh_overloaded = 0

            for t in tasks:
                req_m = float(t["labor_minutes_required"])
                wh_req_labor += req_m
                if rem_labor >= req_m:
                    assn_m = req_m
                    rem_labor -= req_m
                    status = ReplenishmentStatus.PENDING.value
                    rep_qty = int(t["required_quantity"])
                else:
                    assn_m = round(max(0.0, rem_labor), 3)
                    share = (assn_m / req_m) if req_m > 0 else 0.0
                    rep_qty = max(0, int(math.floor(int(t["required_quantity"]) * share)))
                    rem_labor = 0.0
                    status = ReplenishmentStatus.OVERLOADED.value
                    wh_overloaded += 1

                wh_assigned_labor += assn_m
                rep_id = format_replenishment_id(year, next_rep_seq)
                next_rep_seq += 1

                task_record = {
                    "replenishment_id": rep_id,
                    "warehouse_id": wid,
                    "sku_id": t["sku_id"],
                    "source_location_id": t["source_location_id"],
                    "destination_location_id": t["destination_location_id"],
                    "required_quantity": int(t["required_quantity"]),
                    "replenishment_quantity": rep_qty,
                    "priority": t["priority"],
                    "labor_minutes_required": round(req_m, 3),
                    "assigned_labor_minutes": round(assn_m, 3),
                    "replenishment_status": status,
                }
                simulated_tasks.append(task_record)

                if persist_events:
                    session.add(
                        Replenishment(
                            replenishment_id=rep_id,
                            warehouse_id=wid,
                            sku_id=t["sku_id"],
                            source_location_id=t["source_location_id"],
                            destination_location_id=t["destination_location_id"],
                            required_quantity=int(t["required_quantity"]),
                            replenishment_quantity=rep_qty,
                            priority=t["priority"],
                            labor_minutes_required=round(req_m, 3),
                            assigned_labor_minutes=round(assn_m, 3),
                            replenishment_status=status,
                            created_at=now_dt,
                            completed_at=None,
                        )
                    )

            total_req_labor += wh_req_labor
            total_overloaded_tasks += wh_overloaded
            is_wh_overloaded = wh_req_labor > avail_cap + 1e-3
            if is_wh_overloaded:
                overloaded_warehouses.append(wid)

            util_pct = round((wh_req_labor / avail_cap) * 100.0, 2) if avail_cap > 0 else 0.0
            warehouse_summaries.append(
                {
                    "warehouse_id": wid,
                    "task_count": len(tasks),
                    "overloaded_task_count": wh_overloaded,
                    "labor_minutes_required": round(wh_req_labor, 2),
                    "available_labor_minutes": avail_cap,
                    "assigned_labor_minutes": round(wh_assigned_labor, 2),
                    "labor_utilization_pct": util_pct,
                    "is_overloaded": is_wh_overloaded,
                }
            )

        if persist_events and simulated_tasks:
            self.audit_service.log_event(
                session=session,
                event_type="REPLENISHMENT_SIMULATION_PERSISTED",
                entity_type="REPLENISHMENT_BATCH",
                entity_id=optimization_run_id or (warehouse_id or "ALL"),
                actor=actor,
                previous_state=None,
                new_state={
                    "tasks_created": len(simulated_tasks),
                    "overloaded_tasks": total_overloaded_tasks,
                    "overloaded_warehouses": overloaded_warehouses,
                },
                timestamp=now_dt,
            )
            session.flush()

        overall_util = round((total_req_labor / total_avail_labor) * 100.0, 2) if total_avail_labor > 0 else 0.0
        return {
            "warehouse_scope": warehouse_id or "ALL",
            "demand_source": "ML_FORECAST" if use_ml_forecasts else "HISTORICAL_OBSERVED",
            "forecast_adjusted_sku_count": forecast_adjusted_skus if use_ml_forecasts else 0,
            "total_requests": len(simulated_tasks),
            "total_labor_minutes_required": round(total_req_labor, 2),
            "total_available_labor_minutes": round(total_avail_labor, 2),
            "overall_labor_utilization_pct": overall_util,
            "overloaded_task_count": total_overloaded_tasks,
            "overloaded_warehouses": overloaded_warehouses,
            "warehouse_breakdown": warehouse_summaries,
            "tasks": simulated_tasks[:100],
        }
