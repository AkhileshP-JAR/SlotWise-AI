"""Page 01 — Executive Operational Overview & Ground-Truth Benchmark KPIs."""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from frontend.components.api_client import FrontendAPIError
from frontend.components.assistant_panel import render_decision_assistant_panel
from frontend.components.charts import build_velocity_and_abc_figures
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
    client, warehouse_id, warehouses, offline_err = init_page_and_sidebar(
        page_title="01 Overview",
        page_icon="📊",
    )

    render_page_header(
        title="01 · Executive Warehouse & Slotting Overview",
        subtitle="Real-time operational KPIs, SKU velocity/ABC/XYZ distributions, slotting optimization gains, and F1 exception benchmark.",
        scope_label=format_scope_badge(warehouse_id),
    )

    if offline_err is not None:
        render_api_unavailable_banner(offline_err, backend_url=client.base_url)
        return

    sample_orders = st.slider(
        "Order Sample Size for Live Picking Benchmark",
        min_value=50,
        max_value=500,
        value=250,
        step=50,
        key="ov_sample_orders",
    )

    with st.spinner("Computing live executive KPIs from relational operational tables..."):
        try:
            overview = client.get_analytics_overview(
                warehouse_id=warehouse_id,
                sample_picking_orders=sample_orders,
            )
        except FrontendAPIError as err:
            render_api_error(err, title="Unable to Retrieve Executive Overview")
            return

    wh = overview.get("warehouse", {})
    sku = overview.get("sku", {})
    opt = overview.get("optimization", {})
    pick = overview.get("picking_simulated_estimates", {})
    rep = overview.get("replenishment", {})
    exc = overview.get("exceptions", {})
    gt = overview.get("ground_truth_evaluation", {})

    if int(wh.get("total_locations", 0)) == 0 or int(sku.get("total_skus", 0)) == 0:
        render_empty_dataset_banner()
        return

    render_section_header("📌 Observed Operational State (Live Relational Tables)")
    r1c1, r1c2, r1c3, r1c4 = st.columns(4)
    with r1c1:
        render_kpi_card(
            "Slot Occupancy",
            f"{wh.get('occupied_locations', 0):,} / {wh.get('total_locations', 0):,}",
            f"{wh.get('location_utilization_pct', 0.0)}% utilized · {wh.get('available_locations', 0):,} open",
            accent="blue",
        )
    with r1c2:
        render_kpi_card(
            "Physical Capacity Util",
            f"{wh.get('volume_capacity_utilization_pct', 0.0)}% Vol",
            f"{wh.get('weight_capacity_utilization_pct', 0.0)}% Weight · {wh.get('total_zones', 0)} Zones",
            accent="indigo",
        )
    with r1c3:
        render_kpi_card(
            "Replenishment Labor",
            f"{rep.get('labor_utilization_pct', 0.0)}%",
            f"{rep.get('total_workload_minutes', 0.0):,.0f} / {rep.get('available_labor_minutes', 0.0):,.0f} min ({rep.get('overloaded_tasks', 0)} overloaded)",
            accent="amber" if int(rep.get("overloaded_tasks", 0)) > 0 else "emerald",
        )
    with r1c4:
        render_kpi_card(
            "Exception Governance",
            f"{exc.get('open_exceptions', 0)} Open",
            f"{exc.get('critical_and_high_open', 0)} Crit/High · {exc.get('resolved_exceptions', 0)} Resolved",
            accent="rose" if int(exc.get("critical_and_high_open", 0)) > 0 else "emerald",
        )

    render_section_header("⚡ Optimization, Simulated Pick Savings & Detection Accuracy")
    render_telemetry_disclaimer(
        "Telemetry Separation Note: Occupancy, replenishment queue, and exception counts above are observed persisted state. "
        "Pick travel distance/time savings below are deterministic aisle-corridor simulations across identical fulfillment orders; "
        "Precision/Recall/F1 is an isolated read-only benchmark against ground_truth.json."
    )

    r2c1, r2c2, r2c3, r2c4 = st.columns(4)
    with r2c1:
        sr_pct = round(float(opt.get("assignment_success_rate", 0.0)) * 100.0, 2)
        render_kpi_card(
            "Slotting Assignment Rate",
            f"{sr_pct}%",
            f"Run: {opt.get('latest_run_id') or 'Baseline'} ({opt.get('algorithm_name', 'N/A')})",
            accent="emerald",
        )
    with r2c2:
        render_kpi_card(
            "Avg Multi-Factor Score",
            f"{float(opt.get('average_assignment_score', 0.0)):.4f}",
            f"{opt.get('constraint_violation_count', 0)} hard-constraint violations",
            accent="emerald" if int(opt.get("constraint_violation_count", 0)) == 0 else "rose",
        )
    with r2c3:
        render_kpi_card(
            "Simulated Pick Travel Saved",
            f"{pick.get('distance_improvement_pct', 0.0)}%",
            f"{pick.get('distance_saved_m', 0.0):,.1f} m & {pick.get('picking_time_improvement_pct', 0.0)}% time saved ({pick.get('orders_evaluated', 0)} orders)",
            accent="emerald",
        )
    with r2c4:
        f1_val = float(gt.get("f1_score", 0.0))
        render_kpi_card(
            "Anomaly Detection F1",
            f"{f1_val:.4f}" if gt.get("ground_truth_available") else "N/A",
            f"P={gt.get('precision', 0.0):.2f} · R={gt.get('recall', 0.0):.2f} ({gt.get('true_positives', 0)}/{gt.get('expected_anomalies', 0)} TP)",
            accent="emerald" if f1_val >= 0.95 else "amber",
        )

    fig_vel, fig_abc = build_velocity_and_abc_figures(sku)
    ch1, ch2 = st.columns(2)
    with ch1:
        st.plotly_chart(fig_vel, width="stretch")
    with ch2:
        st.plotly_chart(fig_abc, width="stretch")

    if warehouses:
        render_section_header("🏢 Multi-Warehouse Capacity & Utilization Matrix")
        filtered_wh = [w for w in warehouses if warehouse_id is None or w["warehouse_id"] == warehouse_id]
        if filtered_wh:
            fig_wh = go.Figure(
                data=[
                    go.Bar(
                        name="Slot Occupancy %",
                        x=[f"{w['warehouse_id']} ({w['city']})" for w in filtered_wh],
                        y=[float(w["location_utilization_pct"]) for w in filtered_wh],
                        marker_color="#1D4ED8",
                    ),
                    go.Bar(
                        name="Volume Util %",
                        x=[f"{w['warehouse_id']} ({w['city']})" for w in filtered_wh],
                        y=[float(w["volume_utilization_pct"]) for w in filtered_wh],
                        marker_color="#059669",
                    ),
                    go.Bar(
                        name="Weight Util %",
                        x=[f"{w['warehouse_id']} ({w['city']})" for w in filtered_wh],
                        y=[float(w["weight_utilization_pct"]) for w in filtered_wh],
                        marker_color="#D97706",
                    ),
                ]
            )
            fig_wh.update_layout(
                barmode="group",
                height=310,
                margin={"l": 40, "r": 20, "t": 35, "b": 35},
                paper_bgcolor="#FFFFFF",
                plot_bgcolor="#F8FAFC",
                title="Facility Capacity Utilization Comparison (%)",
            )
            st.plotly_chart(fig_wh, width="stretch")

            wh_df = pd.DataFrame(filtered_wh)[
                [
                    "warehouse_id",
                    "warehouse_name",
                    "warehouse_type",
                    "city",
                    "zone_count",
                    "aisle_count",
                    "blocked_aisle_count",
                    "total_locations",
                    "occupied_locations",
                    "location_utilization_pct",
                    "volume_utilization_pct",
                    "daily_labor_minutes_capacity",
                ]
            ]
            st.dataframe(wh_df, width="stretch", hide_index=True)

    st.divider()
    render_decision_assistant_panel(client=client, warehouse_id=warehouse_id, key_prefix="ov_asst")


if __name__ == "__main__":
    main()
