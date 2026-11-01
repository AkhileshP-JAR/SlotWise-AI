"""Page 05 — Order Picking Telemetry, Travel Distance Attribution & 2D Pick-Tour Comparison."""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pandas as pd
import streamlit as st

from frontend.components.api_client import FrontendAPIError
from frontend.components.charts import (
    build_pick_route_comparison_figure,
    build_picking_attribution_figure,
)
from frontend.components.sidebar import format_scope_badge, init_page_and_sidebar
from frontend.components.theme import (
    render_api_error,
    render_api_unavailable_banner,
    render_empty_dataset_banner,
    render_kpi_card,
    render_page_header,
    render_section_header,
    render_telemetry_disclaimer,
)


def main() -> None:
    client, warehouse_id, _warehouses, offline_err = init_page_and_sidebar(
        page_title="05 Picking Analytics",
        page_icon="🚶",
    )

    render_page_header(
        title="05 · Order Picking Telemetry & 2D Pick-Path Optimization",
        subtitle="Compare observed PickEvent telemetry with deterministic Nearest-Neighbor + 2-Opt route and slotting improvements across identical orders.",
        scope_label=format_scope_badge(warehouse_id),
    )

    if offline_err is not None:
        render_api_unavailable_banner(offline_err, backend_url=client.base_url)
        return

    render_telemetry_disclaimer(
        "Metric Attribution Notice: Section 1 reports historical PickEvent records persisted in the database. "
        "Section 2 simulates Baseline vs. Optimized Slotting + Nearest-Neighbor/2-Opt routing over identical customer order lines."
    )

    # Controls for simulation cohort size and optional optimization run target
    try:
        runs_resp = client.list_optimization_runs(page=1, page_size=25)
        runs = runs_resp.get("items", [])
    except FrontendAPIError as err:
        render_api_error(err, title="Failed to Load Optimization Runs")
        return

    ctrl1, ctrl2, ctrl3 = st.columns([1.6, 1.2, 1.4])
    with ctrl1:
        run_opts = ["CURRENT_ACTIVE"] + [r["run_id"] for r in runs]
        chosen_run = st.selectbox(
            "Comparison Target Layout",
            options=run_opts,
            format_func=lambda rid: (
                "Current ACTIVE Slotting Layout" if rid == "CURRENT_ACTIVE" else f"Optimization Run {rid}"
            ),
            key="pick_target_run",
        )
    with ctrl2:
        max_orders = st.slider(
            "Orders Cohort Size (Identical Orders)",
            min_value=25,
            max_value=800,
            value=250,
            step=25,
            key="pick_max_orders",
        )
    with ctrl3:
        explicit_order_input = st.text_input(
            "Optional Specific Order ID(s) (comma-separated)",
            value="",
            placeholder="e.g., ORD-2026-000002",
            key="pick_explicit_orders",
        )

    opt_run_param = None if chosen_run == "CURRENT_ACTIVE" else chosen_run
    explicit_orders = [o.strip() for o in explicit_order_input.split(",") if o.strip()] or None

    with st.spinner("Loading observed PickEvent telemetry and running 2-Opt pick-tour simulation..."):
        try:
            pick_analytics = client.get_picking_analytics(
                warehouse_id=warehouse_id,
                optimization_run_id=opt_run_param,
                max_orders=int(max_orders),
                recent_orders_limit=40,
            )
            if explicit_orders:
                sim = client.simulate_picking(
                    warehouse_id=warehouse_id,
                    optimization_run_id=opt_run_param,
                    order_ids=explicit_orders,
                    max_orders=int(max_orders),
                )
            else:
                sim = pick_analytics.get("simulated_comparison", {})
        except FrontendAPIError as err:
            render_api_error(err, title="Picking Analytics / Simulation Request Failed")
            return

    obs = pick_analytics.get("observed_telemetry", {})
    recent_orders = pick_analytics.get("recent_orders", [])

    if (
        int(obs.get("total_pick_events", 0)) == 0
        and int(sim.get("orders_evaluated", 0)) == 0
        and not recent_orders
    ):
        render_empty_dataset_banner()
        return

    # ------------------------------------------------------------------
    # Section 1: Observed Historical PickEvent Telemetry
    # ------------------------------------------------------------------
    render_section_header("📡 Section 1 · Observed Historical PickEvent Telemetry (Persisted DB)")
    o1, o2, o3, o4 = st.columns(4)
    with o1:
        render_kpi_card(
            "Observed Pick Events",
            f"{int(obs.get('total_pick_events', 0)):,}",
            f"Units Picked: {int(obs.get('total_units_picked', 0)):,}",
            accent="blue",
        )
    with o2:
        render_kpi_card(
            "Observed Total Travel",
            f"{float(obs.get('observed_total_travel_distance_m', 0.0)):,.1f} m",
            "Historical warehouse execution log",
            accent="slate",
        )
    with o3:
        render_kpi_card(
            "Avg Distance / Pick Line",
            f"{float(obs.get('observed_avg_distance_per_pick_m', 0.0)):.2f} m",
            "Per persisted PickEvent",
            accent="indigo",
        )
    with o4:
        status_bd = obs.get("event_status_breakdown", {})
        blocked_ev = int(status_bd.get("BLOCKED_REROUTED", 0)) + int(status_bd.get("BLOCKED", 0))
        render_kpi_card(
            "Avg Pick Duration",
            f"{float(obs.get('observed_avg_duration_seconds', 0.0)):.1f} s",
            f"Status breakdown: {status_bd or {'COMPLETED': 0}} (Blocked={blocked_ev})",
            accent="amber" if blocked_ev > 0 else "emerald",
        )

    # ------------------------------------------------------------------
    # Section 2: Simulated Baseline vs. Optimized Slotting & 2-Opt Routing
    # ------------------------------------------------------------------
    st.divider()
    render_section_header(
        f"🧭 Section 2 · Simulated Pick-Path & Slotting Comparison ({int(sim.get('orders_evaluated', 0)):,} Identical Orders / {int(sim.get('order_lines_evaluated', 0)):,} Lines)"
    )

    s1, s2, s3, s4 = st.columns(4)
    with s1:
        render_kpi_card(
            "Baseline Tour Distance",
            f"{float(sim.get('baseline_distance_m', 0.0)):,.1f} m",
            f"Baseline Time: {float(sim.get('baseline_time_seconds', 0.0)) / 60.0:,.1f} min",
            accent="rose",
        )
    with s2:
        render_kpi_card(
            "Optimized Tour Distance",
            f"{float(sim.get('optimized_distance_m', 0.0)):,.1f} m",
            f"Optimized Time: {float(sim.get('optimized_time_seconds', 0.0)) / 60.0:,.1f} min",
            accent="emerald",
        )
    with s3:
        render_kpi_card(
            "Combined Distance Saved",
            f"{float(sim.get('distance_saved_m', 0.0)):,.1f} m",
            f"{float(sim.get('percentage_improvement', 0.0)):.2f}% reduction (Time -{float(sim.get('time_improvement_pct', 0.0)):.2f}%)",
            accent="emerald",
        )
    with s4:
        render_kpi_card(
            "Pure Slotting Relocation Gain",
            f"{float(sim.get('pure_slotting_distance_saved_m', 0.0)):,.1f} m",
            f"Blocked stops safely bypassed: {int(sim.get('blocked_stops_excluded', 0))}",
            accent="blue",
        )

    st.plotly_chart(build_picking_attribution_figure(sim), width="stretch")

    # ------------------------------------------------------------------
    # Section 3: Interactive 2D Order Pick-Tour Trajectory Visualizer
    # ------------------------------------------------------------------
    st.divider()
    render_section_header("🗺️ Interactive 2D Order Pick-Tour Waypoint Comparison")

    sample_orders = sim.get("sample_orders", [])
    if not sample_orders:
        st.info("No multi-line sample orders matched the selected filter cohort.")
    else:
        selected_ord_id = st.selectbox(
            "Select Sample Customer Order to Compare Baseline vs. Optimized 2D Pick Trajectory",
            options=[so["order_id"] for so in sample_orders],
            format_func=lambda oid: next(
                (
                    f"{so['order_id']} ({so['warehouse_id']} · {so['line_count']} lines) — "
                    f"Baseline {so['baseline_distance_m']:.1f}m → Optimized {so['optimized_distance_m']:.1f}m "
                    f"(Saved {so['distance_saved_m']:.1f}m)"
                    for so in sample_orders
                    if so["order_id"] == oid
                ),
                oid,
            ),
            key="pick_sample_order_select",
        )
        selected_sample = next(so for so in sample_orders if so["order_id"] == selected_ord_id)

        st.plotly_chart(build_pick_route_comparison_figure(selected_sample), width="stretch")

        seq_col1, seq_col2 = st.columns(2)
        with seq_col1:
            st.markdown(f"**Baseline Stop Sequence (`{selected_sample['baseline_distance_m']:.1f} m`):**")
            st.code("DOCK → " + " → ".join(selected_sample.get("baseline_sequence", [])) + " → DOCK")
        with seq_col2:
            st.markdown(f"**Optimized Stop Sequence (`{selected_sample['optimized_distance_m']:.1f} m`):**")
            st.code("DOCK → " + " → ".join(selected_sample.get("optimized_sequence", [])) + " → DOCK")

    if recent_orders:
        with st.expander(f"📋 View Recent Outbound Orders ({len(recent_orders)} shown)", expanded=False):
            st.dataframe(pd.DataFrame(recent_orders), width="stretch", hide_index=True)


if __name__ == "__main__":
    main()
