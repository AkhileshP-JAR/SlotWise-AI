"""Warehouse aisle-network pick-path simulator and Nearest-Neighbor + 2-Opt route optimizer.

Implements Section 8.7:
- Models warehouse docks, aisle corridors, cross-aisles, congestion, and blocked aisles.
- Never treats a blocked aisle or blocked location as a valid route.
- Compares baseline vs. optimized slotting and routing on identical order lines.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import DomainConfig, load_domain_config
from backend.app.models import (
    Aisle,
    Location,
    Order,
    OrderLine,
    SlotAssignment,
    Warehouse,
    Zone,
)
from backend.app.models.enums import AssignmentStatus


@dataclass(frozen=True)
class StopPoint:
    """Physical pick stop inside a warehouse."""

    location_id: str
    sku_id: str
    aisle_id: str
    x: float
    y: float
    y_front: float
    y_rear: float
    aisle_blocked: bool
    location_blocked: bool
    congestion_factor: float
    quantity: int


class PickingSimulationService:
    """Simulates and optimizes order picking routes across baseline and optimized slotting layouts."""

    def __init__(self, config: DomainConfig | None = None) -> None:
        self.config = config or load_domain_config()

    @staticmethod
    def corridor_distance(
        ax: float,
        ay: float,
        a_aisle: str | None,
        a_y_front: float,
        a_y_rear: float,
        bx: float,
        by: float,
        b_aisle: str | None,
        b_y_front: float,
        b_y_rear: float,
        blocked_aisle_xs: list[float] | None = None,
    ) -> float:
        """Compute aisle-corridor Manhattan distance between two points, detouring around blocked aisles."""
        if a_aisle is not None and a_aisle == b_aisle:
            return abs(bx - ax) + abs(by - ay)

        if a_aisle is None or b_aisle is None:
            # Dock-to-aisle or aisle-to-dock transition via front cross-aisle
            base = abs(bx - ax) + abs(by - ay)
        else:
            y_front = min(a_y_front, b_y_front)
            y_rear = max(a_y_rear, b_y_rear)
            via_front = abs(ay - y_front) + abs(by - y_front) + abs(bx - ax)
            via_rear = abs(y_rear - ay) + abs(y_rear - by) + abs(bx - ax)
            base = min(via_front, via_rear)

        # Add perimeter cross-aisle bypass penalty if crossing a blocked corridor header horizontally
        detour = 0.0
        if blocked_aisle_xs:
            low_x, high_x = min(ax, bx), max(ax, bx)
            for blk_x in blocked_aisle_xs:
                if low_x < blk_x < high_x:
                    detour += 2.4
        return round(base + detour, 4)

    def simulate_single_order_tour(
        self,
        depot_x: float,
        depot_y: float,
        stops: list[StopPoint],
        blocked_aisle_xs: list[float] | None = None,
        walk_speed_mps: float | None = None,
    ) -> dict[str, Any]:
        """Compute baseline (entry-order) and optimized (Nearest-Neighbor + 2-Opt) tours for one order."""
        speed = walk_speed_mps or self.config.simulation.picking.default_walk_speed_mps
        line_sec = self.config.simulation.picking.pick_handling_seconds_per_line
        unit_sec = self.config.simulation.picking.pick_handling_seconds_per_unit

        blocked_stops = [s for s in stops if s.aisle_blocked or s.location_blocked]
        valid_stops = [s for s in stops if not s.aisle_blocked and not s.location_blocked]

        if not valid_stops:
            return {
                "valid_stop_count": 0,
                "blocked_stop_count": len(blocked_stops),
                "blocked_location_ids": [s.location_id for s in blocked_stops],
                "baseline_distance_m": 0.0,
                "optimized_distance_m": 0.0,
                "distance_saved_m": 0.0,
                "baseline_time_seconds": 0.0,
                "optimized_time_seconds": 0.0,
                "baseline_waypoints": [(depot_x, depot_y)],
                "optimized_waypoints": [(depot_x, depot_y)],
                "baseline_sequence": [],
                "optimized_sequence": [],
            }

        def _tour_metrics(ordered_stops: list[StopPoint]) -> tuple[float, float, list[tuple[float, float]]]:
            total_dist = 0.0
            total_time = 0.0
            waypoints: list[tuple[float, float]] = [(depot_x, depot_y)]

            prev_x, prev_y = depot_x, depot_y
            prev_aisle: str | None = None
            prev_yf, prev_yr = 2.0, 2.0

            for st in ordered_stops:
                leg_d = self.corridor_distance(
                    prev_x,
                    prev_y,
                    prev_aisle,
                    prev_yf,
                    prev_yr,
                    st.x,
                    st.y,
                    st.aisle_id,
                    st.y_front,
                    st.y_rear,
                    blocked_aisle_xs,
                )
                total_dist += leg_d
                eff_speed = speed / max(1.0, st.congestion_factor)
                total_time += (leg_d / eff_speed) + line_sec + (st.quantity * unit_sec)
                waypoints.append((st.x, st.y))
                prev_x, prev_y, prev_aisle, prev_yf, prev_yr = (
                    st.x,
                    st.y,
                    st.aisle_id,
                    st.y_front,
                    st.y_rear,
                )

            # Return leg to staging dock
            ret_d = self.corridor_distance(
                prev_x,
                prev_y,
                prev_aisle,
                prev_yf,
                prev_yr,
                depot_x,
                depot_y,
                None,
                2.0,
                2.0,
                blocked_aisle_xs,
            )
            total_dist += ret_d
            total_time += ret_d / speed
            waypoints.append((depot_x, depot_y))
            return round(total_dist, 3), round(total_time, 2), waypoints

        # 1. Baseline route: raw order-line entry sequence
        base_dist, base_time, base_wp = _tour_metrics(valid_stops)

        # 2. Consolidate identical locations for optimized tour while summing picked quantities
        consolidated_map: dict[str, StopPoint] = {}
        for st in valid_stops:
            if st.location_id in consolidated_map:
                ex = consolidated_map[st.location_id]
                consolidated_map[st.location_id] = StopPoint(
                    location_id=ex.location_id,
                    sku_id=ex.sku_id,
                    aisle_id=ex.aisle_id,
                    x=ex.x,
                    y=ex.y,
                    y_front=ex.y_front,
                    y_rear=ex.y_rear,
                    aisle_blocked=ex.aisle_blocked,
                    location_blocked=ex.location_blocked,
                    congestion_factor=ex.congestion_factor,
                    quantity=ex.quantity + st.quantity,
                )
            else:
                consolidated_map[st.location_id] = st
        unique_stops = list(consolidated_map.values())

        # Nearest-Neighbor constructive tour from dock
        unvisited = list(unique_stops)
        nn_tour: list[StopPoint] = []
        cx, cy, c_aisle, cyf, cyr = depot_x, depot_y, None, 2.0, 2.0
        while unvisited:
            unvisited.sort(
                key=lambda s: (
                    self.corridor_distance(
                        cx, cy, c_aisle, cyf, cyr, s.x, s.y, s.aisle_id, s.y_front, s.y_rear, blocked_aisle_xs
                    ),
                    s.location_id,
                )
            )
            nxt = unvisited.pop(0)
            nn_tour.append(nxt)
            cx, cy, c_aisle, cyf, cyr = nxt.x, nxt.y, nxt.aisle_id, nxt.y_front, nxt.y_rear

        # Deterministic 2-Opt local search improvement
        best_tour = list(nn_tour)
        best_dist, _, _ = _tour_metrics(best_tour)
        n_stops = len(best_tour)
        if n_stops >= 3:
            max_passes = self.config.simulation.picking.two_opt_max_passes
            for _ in range(max_passes):
                improved = False
                for i in range(n_stops - 1):
                    for k in range(i + 1, n_stops):
                        candidate_tour = best_tour[:i] + list(reversed(best_tour[i : k + 1])) + best_tour[k + 1 :]
                        cand_dist, _, _ = _tour_metrics(candidate_tour)
                        if cand_dist + 1e-4 < best_dist:
                            best_tour = candidate_tour
                            best_dist = cand_dist
                            improved = True
                if not improved:
                    break

        opt_dist, opt_time, opt_wp = _tour_metrics(best_tour)
        # Note: consolidation saves duplicate line-stop setups; ensure fair non-negative comparison
        if opt_dist > base_dist:
            best_tour = valid_stops
            opt_dist, opt_time, opt_wp = base_dist, base_time, base_wp

        return {
            "valid_stop_count": len(valid_stops),
            "blocked_stop_count": len(blocked_stops),
            "blocked_location_ids": [s.location_id for s in blocked_stops],
            "baseline_distance_m": base_dist,
            "optimized_distance_m": opt_dist,
            "distance_saved_m": round(base_dist - opt_dist, 3),
            "baseline_time_seconds": base_time,
            "optimized_time_seconds": opt_time,
            "baseline_waypoints": base_wp,
            "optimized_waypoints": opt_wp,
            "baseline_sequence": [s.location_id for s in valid_stops],
            "optimized_sequence": [s.location_id for s in best_tour],
        }

    def compare_slotting_layouts(
        self,
        session: Session,
        optimization_run_id: str | None = None,
        warehouse_id: str | None = None,
        order_ids: list[str] | None = None,
        max_orders: int = 400,
    ) -> dict[str, Any]:
        """Compare baseline slotting vs. optimized slotting across identical orders and order lines."""
        warehouses = {w.warehouse_id: w for w in session.scalars(select(Warehouse)).all()}
        zones = {z.zone_id: z for z in session.scalars(select(Zone)).all()}
        aisles = {a.aisle_id: a for a in session.scalars(select(Aisle)).all()}
        locations = {loc.location_id: loc for loc in session.scalars(select(Location)).all()}

        # Build blocked aisle x-coordinates per warehouse
        blocked_xs_by_wh: dict[str, list[float]] = {}
        for a in aisles.values():
            if a.is_blocked:
                blocked_xs_by_wh.setdefault(a.warehouse_id, []).append(float(a.x_start))

        # Baseline assignment map: initial legacy assignments (optimization_run_id IS NULL)
        baseline_rows = session.scalars(
            select(SlotAssignment)
            .where(SlotAssignment.optimization_run_id.is_(None))
            .order_by(SlotAssignment.slot_assignment_id)
        ).all()
        baseline_map: dict[tuple[str, str], str] = {}
        for r in baseline_rows:
            baseline_map.setdefault((r.warehouse_id, r.sku_id), r.location_id)

        # Optimized assignment map: from specified optimization_run_id (if provided) overlaid on active layout
        optimized_map: dict[tuple[str, str], str] = {}
        active_rows = session.scalars(
            select(SlotAssignment)
            .where(SlotAssignment.assignment_status == AssignmentStatus.ACTIVE.value)
            .order_by(SlotAssignment.slot_assignment_id)
        ).all()
        for r in active_rows:
            optimized_map[(r.warehouse_id, r.sku_id)] = r.location_id

        if optimization_run_id:
            run_rows = session.scalars(
                select(SlotAssignment)
                .where(SlotAssignment.optimization_run_id == optimization_run_id)
                .order_by(SlotAssignment.slot_assignment_id)
            ).all()
            for r in run_rows:
                optimized_map[(r.warehouse_id, r.sku_id)] = r.location_id

        # Load deterministic order subset
        ord_stmt = select(Order).order_by(Order.order_id)
        if warehouse_id:
            ord_stmt = ord_stmt.where(Order.warehouse_id == warehouse_id)
        if order_ids:
            ord_stmt = ord_stmt.where(Order.order_id.in_(order_ids))
        orders_list = session.scalars(ord_stmt.limit(max_orders)).all()
        if not orders_list:
            return {
                "warehouse_scope": warehouse_id or "ALL",
                "optimization_run_id": optimization_run_id,
                "orders_evaluated": 0,
                "order_lines_evaluated": 0,
                "blocked_stops_excluded": 0,
                "baseline_distance_m": 0.0,
                "baseline_slotting_optimized_route_distance_m": 0.0,
                "optimized_slotting_baseline_route_distance_m": 0.0,
                "optimized_distance_m": 0.0,
                "distance_saved_m": 0.0,
                "percentage_improvement": 0.0,
                "pure_slotting_distance_saved_m": 0.0,
                "baseline_time_seconds": 0.0,
                "optimized_time_seconds": 0.0,
                "estimated_time_saved_seconds": 0.0,
                "time_improvement_pct": 0.0,
                "sample_orders": [],
            }

        target_oids = [o.order_id for o in orders_list]
        lines_orm = session.scalars(
            select(OrderLine).where(OrderLine.order_id.in_(target_oids)).order_by(OrderLine.order_line_id)
        ).all()
        lines_by_oid: dict[str, list[OrderLine]] = {}
        for ln in lines_orm:
            lines_by_oid.setdefault(ln.order_id, []).append(ln)

        def _make_stop(loc_id: str, sku_id: str, qty: int) -> StopPoint | None:
            loc = locations.get(loc_id)
            if loc is None:
                return None
            aisle = aisles[loc.aisle_id]
            _ = zones[loc.zone_id]
            return StopPoint(
                location_id=loc.location_id,
                sku_id=sku_id,
                aisle_id=aisle.aisle_id,
                x=float(loc.x_coordinate),
                y=float(loc.y_coordinate),
                y_front=float(aisle.y_start),
                y_rear=float(aisle.y_end),
                aisle_blocked=bool(aisle.is_blocked),
                location_blocked=bool(loc.is_blocked),
                congestion_factor=float(aisle.congestion_factor),
                quantity=qty,
            )

        tot_base_slot_base_route_d = 0.0
        tot_base_slot_opt_route_d = 0.0
        tot_opt_slot_base_route_d = 0.0
        tot_opt_slot_opt_route_d = 0.0
        tot_base_time_s = 0.0
        tot_opt_time_s = 0.0
        orders_evaluated = 0
        lines_evaluated = 0
        blocked_stops_detected = 0
        sample_orders: list[dict[str, Any]] = []

        for ord_obj in orders_list:
            wid = ord_obj.warehouse_id
            wh = warehouses[wid]
            o_lines = lines_by_oid.get(ord_obj.order_id, [])

            base_stops: list[StopPoint] = []
            opt_stops: list[StopPoint] = []

            # Evaluate strictly identical order lines mapped in both baseline and optimized layouts
            for ln in o_lines:
                b_lid = baseline_map.get((wid, ln.sku_id))
                o_lid = optimized_map.get((wid, ln.sku_id))
                if b_lid is None or o_lid is None:
                    continue
                b_sp = _make_stop(b_lid, ln.sku_id, int(ln.quantity))
                o_sp = _make_stop(o_lid, ln.sku_id, int(ln.quantity))
                if b_sp is None or o_sp is None:
                    continue
                if b_sp.aisle_blocked or b_sp.location_blocked or o_sp.aisle_blocked or o_sp.location_blocked:
                    blocked_stops_detected += 1
                    continue
                base_stops.append(b_sp)
                opt_stops.append(o_sp)

            if not base_stops:
                continue

            blk_xs = blocked_xs_by_wh.get(wid, [])
            base_res = self.simulate_single_order_tour(
                depot_x=float(wh.depot_x),
                depot_y=float(wh.depot_y),
                stops=base_stops,
                blocked_aisle_xs=blk_xs,
            )
            opt_res = self.simulate_single_order_tour(
                depot_x=float(wh.depot_x),
                depot_y=float(wh.depot_y),
                stops=opt_stops,
                blocked_aisle_xs=blk_xs,
            )

            orders_evaluated += 1
            lines_evaluated += len(base_stops)
            tot_base_slot_base_route_d += base_res["baseline_distance_m"]
            tot_base_slot_opt_route_d += base_res["optimized_distance_m"]
            tot_opt_slot_base_route_d += opt_res["baseline_distance_m"]
            tot_opt_slot_opt_route_d += opt_res["optimized_distance_m"]
            tot_base_time_s += base_res["baseline_time_seconds"]
            tot_opt_time_s += opt_res["optimized_time_seconds"]

            if len(sample_orders) < 15:
                sample_orders.append(
                    {
                        "order_id": ord_obj.order_id,
                        "warehouse_id": wid,
                        "line_count": len(base_stops),
                        "baseline_distance_m": base_res["baseline_distance_m"],
                        "optimized_distance_m": opt_res["optimized_distance_m"],
                        "distance_saved_m": round(
                            base_res["baseline_distance_m"] - opt_res["optimized_distance_m"], 3
                        ),
                        "baseline_time_seconds": base_res["baseline_time_seconds"],
                        "optimized_time_seconds": opt_res["optimized_time_seconds"],
                        "baseline_sequence": base_res["baseline_sequence"],
                        "optimized_sequence": opt_res["optimized_sequence"],
                        "baseline_waypoints": base_res["baseline_waypoints"],
                        "optimized_waypoints": opt_res["optimized_waypoints"],
                    }
                )

        dist_saved = round(tot_base_slot_base_route_d - tot_opt_slot_opt_route_d, 2)
        pct_improv = (
            round((dist_saved / tot_base_slot_base_route_d) * 100.0, 2) if tot_base_slot_base_route_d > 0 else 0.0
        )
        time_saved = round(tot_base_time_s - tot_opt_time_s, 2)
        time_pct = round((time_saved / tot_base_time_s) * 100.0, 2) if tot_base_time_s > 0 else 0.0

        return {
            "warehouse_scope": warehouse_id or "ALL",
            "optimization_run_id": optimization_run_id,
            "orders_evaluated": orders_evaluated,
            "order_lines_evaluated": lines_evaluated,
            "blocked_stops_excluded": blocked_stops_detected,
            "baseline_distance_m": round(tot_base_slot_base_route_d, 2),
            "baseline_slotting_optimized_route_distance_m": round(tot_base_slot_opt_route_d, 2),
            "optimized_slotting_baseline_route_distance_m": round(tot_opt_slot_base_route_d, 2),
            "optimized_distance_m": round(tot_opt_slot_opt_route_d, 2),
            "distance_saved_m": dist_saved,
            "percentage_improvement": pct_improv,
            "pure_slotting_distance_saved_m": round(tot_base_slot_base_route_d - tot_opt_slot_base_route_d, 2),
            "baseline_time_seconds": round(tot_base_time_s, 2),
            "optimized_time_seconds": round(tot_opt_time_s, 2),
            "estimated_time_saved_seconds": time_saved,
            "time_improvement_pct": time_pct,
            "sample_orders": sample_orders,
        }
