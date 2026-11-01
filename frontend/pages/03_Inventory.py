"""Page 03 — SKU Inventory Catalog, Analytical Profiling, Demand Trends & Candidate Slot Preview."""

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
from frontend.components.charts import (
    build_candidate_score_comparison_figure,
    build_rejection_reasons_figure,
    build_sku_demand_trend_figure,
)
from frontend.components.sidebar import format_scope_badge, init_page_and_sidebar
from frontend.components.theme import (
    render_api_error,
    render_api_unavailable_banner,
    render_empty_dataset_banner,
    render_kpi_card,
    render_page_header,
    render_section_header,
    status_pill_html,
)


def main() -> None:
    client, warehouse_id, _warehouses, offline_err = init_page_and_sidebar(
        page_title="03 Inventory & SKUs",
        page_icon="📦",
    )

    render_page_header(
        title="03 · SKU Master Catalog, Demand Velocity & Candidate Profiler",
        subtitle="Search and filter SKUs by ABC/XYZ tier, hazard class, thermal regime, and inspect 90-day demand trends and candidate slot rankings.",
        scope_label=format_scope_badge(warehouse_id),
    )

    if offline_err is not None:
        render_api_unavailable_banner(offline_err, backend_url=client.base_url)
        return

    f1, f2, f3, f4, f5, f6 = st.columns([1.6, 1.0, 0.9, 1.1, 1.1, 1.1])
    with f1:
        search_q = st.text_input(
            "Search SKU ID / Name / Category", value="", placeholder="e.g., SKU-000001 or PHARMA", key="inv_search"
        )
    with f2:
        vel_filter = st.selectbox("Velocity Tier", options=["ALL", "FAST", "MEDIUM", "SLOW"], key="inv_vel")
    with f3:
        abc_filter = st.selectbox("ABC Class", options=["ALL", "A", "B", "C"], key="inv_abc")
    with f4:
        haz_filter = st.selectbox(
            "Hazard Class",
            options=["ALL", "NONE", "FLAMMABLE", "CORROSIVE", "OXIDIZER", "TOXIC", "LITHIUM_BATTERY"],
            key="inv_haz",
        )
    with f5:
        temp_filter = st.selectbox(
            "Thermal Regime",
            options=["ALL", "AMBIENT", "CONTROLLED_ROOM", "CHILLED", "FROZEN"],
            key="inv_temp",
        )
    with f6:
        sort_by = st.selectbox(
            "Sort By",
            options=[
                "pick_frequency",
                "average_daily_demand",
                "velocity_percentile",
                "sku_id",
                "unit_price",
                "unit_weight_kg",
            ],
            key="inv_sort_by",
        )

    p_col1, p_col2, p_col3 = st.columns([1, 1, 3])
    with p_col1:
        sort_order = st.selectbox("Sort Order", options=["desc", "asc"], key="inv_sort_order")
    with p_col2:
        page_num = st.number_input("Page", min_value=1, value=1, step=1, key="inv_page")

    try:
        sku_page = client.list_skus(
            search=search_q.strip() or None,
            demand_velocity=None if vel_filter == "ALL" else vel_filter,
            abc_class=None if abc_filter == "ALL" else abc_filter,
            hazard_class=None if haz_filter == "ALL" else haz_filter,
            temperature_requirement=None if temp_filter == "ALL" else temp_filter,
            warehouse_id=warehouse_id,
            sort_by=sort_by,
            sort_order=sort_order,
            page=int(page_num),
            page_size=50,
        )
    except FrontendAPIError as err:
        render_api_error(err, title="SKU Catalog Query Failed")
        return

    skus = sku_page.get("items", [])
    meta = sku_page.get("meta", {})
    total_matching = int(meta.get("total_items", 0))

    if total_matching == 0 and not any(
        [
            search_q.strip(),
            vel_filter != "ALL",
            abc_filter != "ALL",
            haz_filter != "ALL",
            temp_filter != "ALL",
            warehouse_id,
        ]
    ):
        render_empty_dataset_banner()
        return

    render_section_header(
        f"📋 Filtered SKU Catalog ({total_matching:,} Matching SKUs — Page {meta.get('page', 1)} of {max(1, int(meta.get('total_pages', 1)))})"
    )
    if not skus:
        st.info("No SKUs matched the active search and filter combination. Adjust your filters above.")
        return

    st.dataframe(
        pd.DataFrame(skus)[
            [
                "sku_id",
                "sku_name",
                "category",
                "demand_velocity",
                "abc_class",
                "xyz_class",
                "average_daily_demand",
                "pick_frequency",
                "hazard_class",
                "temperature_requirement",
                "unit_weight_kg",
                "active_warehouse_id",
                "active_location_id",
            ]
        ],
        width="stretch",
        hide_index=True,
    )

    st.divider()
    render_section_header("🔬 Interactive SKU Intelligence, 90-Day Demand & Candidate Slot Analyzer")

    selected_sku_id = st.selectbox(
        "Select SKU to Inspect Full Profile & Candidate Slot Rankings",
        options=[s["sku_id"] for s in skus],
        format_func=lambda sid: next(
            (
                f"{s['sku_id']} — {s['sku_name']} [{s['category']} | {s['demand_velocity']} | ABC={s['abc_class']}/{s['xyz_class']} | {s['hazard_class']}]"
                for s in skus
                if s["sku_id"] == sid
            ),
            sid,
        ),
        key="inv_selected_sku",
    )

    with st.spinner(
        f"Retrieving demand history and evaluating 500+ candidate constraints for {selected_sku_id}..."
    ):
        try:
            sku_detail = client.get_sku(selected_sku_id)
            cand_data = client.get_sku_candidates(
                sku_id=selected_sku_id,
                warehouse_id=warehouse_id,
                top_n=10,
                max_rejected_samples=15,
            )
        except FrontendAPIError as err:
            render_api_error(err, title=f"Failed to Profile {selected_sku_id}")
            return

    prof = sku_detail.get("profile") or {}
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        render_kpi_card(
            "Velocity & Pareto Class",
            f"{prof.get('demand_velocity', 'N/A')} · {prof.get('abc_class', '-')}/{prof.get('xyz_class', '-')}",
            f"Percentile: {float(prof.get('velocity_percentile', 0.0)) * 100:.1f}th",
            accent="blue",
        )
    with c2:
        render_kpi_card(
            "Demand & Pick Rate",
            f"{float(prof.get('average_daily_demand', 0.0)):.2f} u/day",
            f"Pick Freq: {float(prof.get('pick_frequency', 0.0)):.2f} lines/day (σ={float(prof.get('demand_std_dev', 0.0)):.2f})",
            accent="indigo",
        )
    with c3:
        render_kpi_card(
            "Inventory & Reorder Point",
            f"On-Hand: {prof.get('current_on_hand_qty', 0)}",
            f"ROP: {float(prof.get('reorder_point', 0.0)):.1f} · Safety Stock: {float(prof.get('safety_stock', 0.0)):.1f}",
            accent="emerald",
        )
    with c4:
        feas_cnt = int(cand_data.get("total_feasible_count", 0))
        eval_cnt = int(cand_data.get("total_locations_evaluated", 0))
        render_kpi_card(
            "Feasible Candidate Slots",
            f"{feas_cnt} / {eval_cnt}",
            f"{cand_data.get('total_rejected_count', 0)} rejected by hard constraints",
            accent="emerald" if feas_cnt > 0 else "rose",
        )

    st.markdown(
        f"**Physical Specs:** `{sku_detail['width_cm']} × {sku_detail['height_cm']} × {sku_detail['depth_cm']} cm` "
        f"(`{sku_detail['unit_volume_m3']} m³`, `{sku_detail['unit_weight_kg']} kg`) &nbsp;|&nbsp; "
        f"**Thermal:** `{sku_detail['temperature_requirement']}` &nbsp;|&nbsp; "
        f"**Hazard:** {status_pill_html(sku_detail['hazard_class'])} &nbsp;|&nbsp; "
        f"**Fragile:** `{'Yes' if sku_detail['is_fragile'] else 'No'}` &nbsp;|&nbsp; "
        f"**Supplier:** `{sku_detail.get('supplier_name') or sku_detail.get('supplier_id')}` "
        f"(Lead Time `{sku_detail.get('supplier_lead_time_days') or 'N/A'} days`)",
        unsafe_allow_html=True,
    )

    trend = sku_detail.get("demand_trend", [])
    if trend:
        st.plotly_chart(
            build_sku_demand_trend_figure(
                sku_id=selected_sku_id,
                demand_trend=trend,
                avg_daily_demand=float(prof.get("average_daily_demand", 0.0)),
            ),
            width="stretch",
        )
    else:
        st.info("No historical order lines recorded for this SKU during the observation window.")

    tab_feas, tab_rej, tab_hist, tab_ml = st.tabs(
        [
            f"✅ Top Ranked Feasible Slots ({len(cand_data.get('top_feasible', []))})",
            f"🚫 Hard-Constraint Rejections ({cand_data.get('total_rejected_count', 0)})",
            f"📜 Assignment Lineage ({len(sku_detail.get('assignment_history', []))})",
            "📈 14-Day ML Demand Forecast",
        ]
    )

    with tab_feas:
        top_feas = cand_data.get("top_feasible", [])
        if top_feas:
            st.plotly_chart(build_candidate_score_comparison_figure(top_feas), width="stretch")
            feas_rows = [
                {
                    "rank": c["rank_position"],
                    "location_id": c["location_id"],
                    "warehouse_id": c["warehouse_id"],
                    "zone_type": c["zone_type"],
                    "accessibility": c["accessibility_type"],
                    "qty": c["assigned_quantity"],
                    "total_score": c["total_score"],
                    "dock_dist_m": c["score_breakdown"]["dock_distance_m"],
                    "explanation": c["score_breakdown"]["explanation"],
                }
                for c in top_feas
            ]
            st.dataframe(pd.DataFrame(feas_rows), width="stretch", hide_index=True)
        else:
            st.error(
                f"**Zero Feasible Slots Found for `{selected_sku_id}`** — Every evaluated location failed at least one hard physical, dimensional, hazard, or temperature constraint. Inspect the **Hard-Constraint Rejections** tab for exact codes."
            )

    with tab_rej:
        rej_counts = cand_data.get("rejection_reason_counts", {})
        if rej_counts:
            st.plotly_chart(build_rejection_reasons_figure(rej_counts), width="stretch")
        rej_samples = cand_data.get("rejected_samples", [])
        if rej_samples:
            rej_rows = [
                {
                    "location_id": r["location_id"],
                    "warehouse_id": r["warehouse_id"],
                    "zone_type": r["zone_type"],
                    "total_score": r["total_score"],
                    "rejection_reasons": ", ".join(r["rejection_reasons"]),
                    "explanation": r["score_breakdown"]["explanation"],
                }
                for r in rej_samples
            ]
            st.dataframe(pd.DataFrame(rej_rows), width="stretch", hide_index=True)
        else:
            st.success("Zero locations were rejected for this SKU.")

    with tab_hist:
        hist = sku_detail.get("assignment_history", [])
        if hist:
            st.dataframe(pd.DataFrame(hist), width="stretch", hide_index=True)
        else:
            st.info("No active or historical slot assignments exist for this SKU.")

    with tab_ml:
        try:
            ml_resp = client.list_ml_forecasts(sku_id=selected_sku_id, page=1, page_size=1)
            ml_items = ml_resp.get("items", [])
        except FrontendAPIError:
            ml_items = []

        if not ml_items:
            st.info(
                "No persisted ML demand forecast found for this SKU yet. Train the chronological SKU demand forecaster "
                "on **09 · System Health → ML Forecasting & MLflow Leaderboard** or run `python scripts/train_forecaster.py`."
            )
        else:
            fc = ml_items[0]
            mc1, mc2, mc3, mc4 = st.columns(4)
            with mc1:
                render_kpi_card(
                    "Forecast Daily Demand",
                    f"{float(fc.get('forecast_daily_demand', 0.0)):.2f} u/day",
                    f"Historical ADV: {float(fc.get('historical_daily_demand', 0.0)):.2f} u/day",
                    accent="blue",
                )
            with mc2:
                sr = float(fc.get("surge_ratio", 1.0))
                render_kpi_card(
                    "Demand Surge Ratio",
                    f"{sr:.2f}×",
                    f"{fc.get('forecast_horizon_days', 14)}d Total: {float(fc.get('forecast_total_units', 0.0)):.1f} u",
                    accent="amber" if sr >= 1.25 else "emerald",
                )
            with mc3:
                render_kpi_card(
                    "Forecast Velocity Tier",
                    str(fc.get("forecast_velocity", "N/A")),
                    f"Historical: {fc.get('historical_velocity', 'N/A')} ({'Shift' if fc.get('velocity_tier_changed') else 'Stable'})",
                    accent="indigo",
                )
            with mc4:
                render_kpi_card(
                    "Forecast Reorder Point",
                    f"{float(fc.get('forecast_reorder_point', 0.0)):.1f} u",
                    f"Historical ROP: {float(fc.get('historical_reorder_point', 0.0)):.1f} u",
                    accent="emerald",
                )

            st.markdown(
                f"**Slotting Guidance:** `{fc.get('slotting_recommendation')}` &nbsp;|&nbsp; "
                f"**Replenishment Guidance:** `{fc.get('replenishment_recommendation')}`"
            )

            traj = fc.get("daily_forecast_trajectory") or []
            if traj:
                dates = [t["forecast_date"] for t in traj]
                preds = [float(t["predicted_units"]) for t in traj]
                fig_fc = go.Figure()
                fig_fc.add_trace(
                    go.Scatter(
                        x=dates,
                        y=preds,
                        mode="lines+markers",
                        name="ML Predicted Units/Day",
                        line={"color": "#2563EB", "width": 2.5},
                        marker={"size": 7},
                    )
                )
                fig_fc.add_hline(
                    y=float(fc.get("historical_daily_demand", 0.0)),
                    line_dash="dash",
                    line_color="#64748B",
                    annotation_text="Historical Avg Daily Demand",
                )
                fig_fc.update_layout(
                    title={
                        "text": f"{selected_sku_id} — {fc.get('forecast_horizon_days', 14)}-Day Multi-Step ML Demand Trajectory",
                        "font": {"size": 15, "color": "#0F172A"},
                    },
                    height=300,
                    margin={"l": 45, "r": 25, "t": 48, "b": 40},
                    paper_bgcolor="#FFFFFF",
                    plot_bgcolor="#F8FAFC",
                    xaxis={"title": "Forecast Date", "gridcolor": "#E2E8F0"},
                    yaxis={"title": "Predicted Units", "gridcolor": "#E2E8F0"},
                )
                st.plotly_chart(fig_fc, width="stretch")


if __name__ == "__main__":
    main()
