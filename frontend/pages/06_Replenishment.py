"""Page 06 — Forward-Pick Replenishment Queue, Shift Labor Utilization & What-If Workload Simulator."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from frontend.components.api_client import FrontendAPIError
from frontend.components.charts import build_replenishment_labor_figure
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


def _build_priority_and_status_figures(
    priority_dist: dict[str, int],
    status_dist: dict[str, int],
) -> tuple[go.Figure, go.Figure]:
    pri_order = ["CRITICAL", "HIGH", "NORMAL", "LOW"]
    pri_keys = [k for k in pri_order if k in priority_dist] + [k for k in priority_dist if k not in pri_order]
    pri_vals = [int(priority_dist.get(k, 0)) for k in pri_keys]
    pri_colors = [
        "#DC2626"
        if k == "CRITICAL"
        else ("#EA580C" if k == "HIGH" else ("#1D4ED8" if k == "NORMAL" else "#64748B"))
        for k in pri_keys
    ]

    fig_pri = go.Figure(
        data=[
            go.Bar(
                x=pri_keys,
                y=pri_vals,
                marker_color=pri_colors,
                text=pri_vals,
                textposition="auto",
            )
        ]
    )
    fig_pri.update_layout(
        title={"text": "Replenishment Queue by Priority Tier", "font": {"size": 15, "color": "#0F172A"}},
        height=320,
        margin={"l": 45, "r": 25, "t": 48, "b": 42},
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#F8FAFC",
        yaxis={"title": "Task Count", "gridcolor": "#E2E8F0"},
        xaxis={"gridcolor": "#E2E8F0"},
    )

    st_keys = list(status_dist.keys())
    st_vals = [int(status_dist[k]) for k in st_keys]
    fig_st = go.Figure(
        data=[
            go.Pie(
                labels=st_keys,
                values=st_vals,
                hole=0.5,
                marker={
                    "colors": [
                        "#DC2626"
                        if k == "OVERLOADED"
                        else (
                            "#059669"
                            if k == "COMPLETED"
                            else ("#D97706" if k in ("PENDING", "IN_PROGRESS") else "#64748B")
                        )
                        for k in st_keys
                    ]
                },
            )
        ]
    )
    fig_st.update_layout(
        title={"text": "Replenishment Execution Status", "font": {"size": 15, "color": "#0F172A"}},
        height=320,
        margin={"l": 35, "r": 25, "t": 48, "b": 35},
        paper_bgcolor="#FFFFFF",
    )
    return fig_pri, fig_st


def main() -> None:
    client, warehouse_id, _warehouses, offline_err = init_page_and_sidebar(
        page_title="06 Replenishment",
        page_icon="🔄",
    )

    render_page_header(
        title="06 · Forward-Pick Replenishment & Shift Labor Capacity",
        subtitle="Monitor persisted reorder-point replenishment queues, warehouse shift labor utilization, and run counterfactual staffing simulations.",
        scope_label=format_scope_badge(warehouse_id),
    )

    if offline_err is not None:
        render_api_unavailable_banner(offline_err, backend_url=client.base_url)
        return

    render_telemetry_disclaimer(
        "Section 1 displays persisted replenishment tasks in the operational database. "
        "Section 2 executes live What-If shift staffing and slotting simulations without overwriting database queues unless explicitly checked."
    )

    # Filter bar for persisted replenishment queue
    f1, f2, f3 = st.columns([1.2, 1.2, 1.0])
    with f1:
        pri_filter = st.selectbox(
            "Filter Queue by Priority",
            options=["ALL", "CRITICAL", "HIGH", "NORMAL", "LOW"],
            key="rep_pri_filter",
        )
    with f2:
        stat_filter = st.selectbox(
            "Filter Queue by Status",
            options=["ALL", "PENDING", "IN_PROGRESS", "OVERLOADED", "COMPLETED", "CANCELLED"],
            key="rep_stat_filter",
        )
    with f3:
        page_num = st.number_input("Queue Page", min_value=1, value=1, step=1, key="rep_page_num")

    try:
        rep_data = client.get_replenishment_analytics(
            warehouse_id=warehouse_id,
            priority=None if pri_filter == "ALL" else pri_filter,
            replenishment_status=None if stat_filter == "ALL" else stat_filter,
            page=int(page_num),
            page_size=75,
        )
        sku_probe = client.list_skus(page=1, page_size=1)
        total_skus = int(sku_probe.get("meta", {}).get("total_items", 0))
    except FrontendAPIError as err:
        render_api_error(err, title="Replenishment Analytics Query Failed")
        return

    if total_skus == 0 and int(rep_data.get("total_tasks", 0)) == 0:
        render_empty_dataset_banner()
        return

    # ------------------------------------------------------------------
    # Section 1: Persisted Replenishment Queue & Warehouse Shift Labor
    # ------------------------------------------------------------------
    render_section_header("📊 Section 1 · Persisted Replenishment Queue & Shift Labor Utilization")

    wh_summaries = rep_data.get("warehouse_labor_summary", [])
    tot_req_min = sum(float(w.get("labor_minutes_required", 0.0)) for w in wh_summaries)
    tot_avail_min = sum(float(w.get("available_labor_minutes", 0.0)) for w in wh_summaries)
    overall_util = round((tot_req_min / tot_avail_min) * 100.0, 2) if tot_avail_min > 0 else 0.0
    overloaded_whs = [w["warehouse_id"] for w in wh_summaries if w.get("is_overloaded")]

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        render_kpi_card(
            "Total Replenishment Tasks",
            f"{int(rep_data.get('total_tasks', 0)):,}",
            f"Active / Open Queue: {int(rep_data.get('active_tasks', 0)):,}",
            accent="blue",
        )
    with k2:
        render_kpi_card(
            "Required Labor Workload",
            f"{tot_req_min:,.1f} min",
            f"Daily Shift Capacity: {tot_avail_min:,.1f} min",
            accent="indigo",
        )
    with k3:
        render_kpi_card(
            "Overall Shift Labor Util",
            f"{overall_util:.1f}%",
            f"Overloaded Facilities: {', '.join(overloaded_whs) if overloaded_whs else 'None (0)'}",
            accent="rose" if overloaded_whs else ("amber" if overall_util >= 80.0 else "emerald"),
        )
    with k4:
        ov_tasks = int(rep_data.get("overloaded_tasks", 0))
        render_kpi_card(
            "Overloaded Tasks",
            f"{ov_tasks:,}",
            "Deferred beyond daily shift labor cap",
            accent="rose" if ov_tasks > 0 else "emerald",
        )

    if wh_summaries:
        st.plotly_chart(build_replenishment_labor_figure(wh_summaries), width="stretch")

    pri_dist = rep_data.get("priority_distribution", {})
    stat_dist = rep_data.get("status_distribution", {})
    if pri_dist or stat_dist:
        fig_pri, fig_st = _build_priority_and_status_figures(pri_dist, stat_dist)
        ch1, ch2 = st.columns(2)
        with ch1:
            st.plotly_chart(fig_pri, width="stretch")
        with ch2:
            st.plotly_chart(fig_st, width="stretch")

    queue_rows = rep_data.get("queue_page", [])
    if queue_rows:
        st.dataframe(
            pd.DataFrame(queue_rows)[
                [
                    "replenishment_id",
                    "warehouse_id",
                    "sku_id",
                    "source_location_id",
                    "destination_location_id",
                    "required_quantity",
                    "replenishment_quantity",
                    "priority",
                    "labor_minutes_required",
                    "assigned_labor_minutes",
                    "replenishment_status",
                ]
            ],
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("No persisted replenishment tasks matched the selected Priority / Status filter.")

    # ------------------------------------------------------------------
    # Section 2: Interactive What-If Shift Staffing & Layout Simulator
    # ------------------------------------------------------------------
    st.divider()
    render_section_header("🧪 Section 2 · Counterfactual Replenishment & Shift Staffing Simulator")

    try:
        runs = client.list_optimization_runs(page=1, page_size=25).get("items", [])
    except FrontendAPIError:
        runs = []

    with st.container(border=True):
        s1, s2, s3 = st.columns([1.4, 1.4, 1.2])
        with s1:
            layout_choice = st.selectbox(
                "Slotting Layout to Simulate",
                options=["ACTIVE", "BASELINE"] + [f"RUN:{r['run_id']}" for r in runs],
                format_func=lambda opt: (
                    "Current ACTIVE Slotting Layout"
                    if opt == "ACTIVE"
                    else (
                        "Pre-Optimization Baseline Layout"
                        if opt == "BASELINE"
                        else f"Optimization Run {opt.split(':', 1)[1]}"
                    )
                ),
                key="rep_sim_layout",
            )
        with s2:
            labor_mult = st.slider(
                "Shift Labor Capacity Multiplier (0.25× Stress Test → 2.50× Surge Staffing)",
                min_value=0.25,
                max_value=2.50,
                value=1.00,
                step=0.05,
                key="rep_sim_mult",
            )
        with s3:
            use_ml_forecasts = st.checkbox(
                "Use ML Demand Forecasts (Forecast-Guided ROP)",
                value=False,
                key="rep_sim_use_ml",
            )
            persist_flag = st.checkbox(
                "Persist Simulated Tasks to Database",
                value=False,
                key="rep_sim_persist",
            )
            run_sim_btn = st.button(
                "▶️ Run Replenishment Simulation", type="primary", width="stretch", key="btn_run_rep_sim"
            )

    if run_sim_btn:
        use_base = layout_choice == "BASELINE"
        opt_run_id = layout_choice.split(":", 1)[1] if layout_choice.startswith("RUN:") else None
        with st.spinner(
            "Simulating reorder-point triggers, reserve-to-forward travel distances, and shift labor allocation..."
        ):
            try:
                sim_out = client.simulate_replenishment(
                    warehouse_id=warehouse_id,
                    optimization_run_id=opt_run_id,
                    use_baseline_assignments=use_base,
                    labor_capacity_multiplier=float(labor_mult),
                    persist_events=persist_flag,
                    use_ml_forecasts=use_ml_forecasts,
                )
                st.session_state["last_rep_simulation"] = sim_out
            except FrontendAPIError as err:
                render_api_error(err, title="Replenishment Simulation Error")

    sim_res: dict[str, Any] | None = st.session_state.get("last_rep_simulation")
    if sim_res:
        m1, m2, m3, m4 = st.columns(4)
        with m1:
            render_kpi_card(
                "Simulated Requests",
                f"{int(sim_res.get('total_requests', 0)):,}",
                f"Scope: {sim_res.get('warehouse_scope', 'ALL')}",
                accent="blue",
            )
        with m2:
            render_kpi_card(
                "Simulated Labor Demand",
                f"{float(sim_res.get('total_labor_minutes_required', 0.0)):,.1f} min",
                f"Scaled Shift Cap: {float(sim_res.get('total_available_labor_minutes', 0.0)):,.1f} min",
                accent="indigo",
            )
        with m3:
            u_pct = float(sim_res.get("overall_labor_utilization_pct", 0.0))
            render_kpi_card(
                "Simulated Labor Util %",
                f"{u_pct:.1f}%",
                f"Overloaded WHs: {', '.join(sim_res.get('overloaded_warehouses') or []) or 'None'}",
                accent="rose" if sim_res.get("overloaded_warehouses") else "emerald",
            )
        with m4:
            ov_cnt = int(sim_res.get("overloaded_task_count", 0))
            render_kpi_card(
                "Simulated Overloaded Tasks",
                f"{ov_cnt:,}",
                "Tasks exceeding scaled daily shift budget",
                accent="rose" if ov_cnt > 0 else "emerald",
            )

        if sim_res.get("warehouse_breakdown"):
            st.plotly_chart(build_replenishment_labor_figure(sim_res["warehouse_breakdown"]), width="stretch")

        if sim_res.get("tasks"):
            with st.expander(
                f"📋 Simulated Replenishment Task Schedule ({len(sim_res['tasks'])} tasks)", expanded=False
            ):
                st.dataframe(pd.DataFrame(sim_res["tasks"]), width="stretch", hide_index=True)


if __name__ == "__main__":
    main()
