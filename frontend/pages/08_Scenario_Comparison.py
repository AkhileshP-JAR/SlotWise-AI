"""Page 08 — Persisted Baseline vs. Optimized Slotting Scenario Comparison & Attribution."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pandas as pd
import streamlit as st

from frontend.components.api_client import FrontendAPIError
from frontend.components.charts import build_scenario_comparison_figures
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


def _build_comparison_table(scenario: dict[str, Any]) -> pd.DataFrame:
    base = scenario.get("baseline_metrics_json") or {}
    opt = scenario.get("optimized_metrics_json") or {}
    rows = [
        ("Assigned SKUs", base.get("assignment_count", 0), opt.get("assignment_count", 0), "SKUs"),
        (
            "Occupied Storage Locations",
            base.get("occupied_locations", 0),
            opt.get("occupied_locations", 0),
            "slots",
        ),
        (
            "Slot Occupancy (%)",
            base.get("location_occupancy_pct", 0.0),
            opt.get("location_occupancy_pct", 0.0),
            "%",
        ),
        (
            "Weight Capacity Util (%)",
            base.get("weight_utilization_pct", 0.0),
            opt.get("weight_utilization_pct", 0.0),
            "%",
        ),
        (
            "Volume Capacity Util (%)",
            base.get("volume_utilization_pct", 0.0),
            opt.get("volume_utilization_pct", 0.0),
            "%",
        ),
        (
            "Average Assignment Score",
            base.get("average_assignment_score", 0.0),
            opt.get("average_assignment_score", 0.0),
            "score [0–1]",
        ),
        (
            "Capacity Violation Locations",
            base.get("capacity_violation_locations", 0),
            opt.get("capacity_violation_locations", 0),
            "locations",
        ),
        ("Total Order Pick Distance", base.get("pick_distance_m", 0.0), opt.get("pick_distance_m", 0.0), "meters"),
        (
            "Total Order Pick Time",
            base.get("pick_time_seconds", 0.0),
            opt.get("pick_time_seconds", 0.0),
            "seconds",
        ),
        (
            "Replenishment Requests",
            base.get("replenishment_requests", 0),
            opt.get("replenishment_requests", 0),
            "tasks",
        ),
        (
            "Replenishment Labor Demand",
            base.get("replenishment_labor_minutes", 0.0),
            opt.get("replenishment_labor_minutes", 0.0),
            "minutes",
        ),
        (
            "Overloaded Replenishment Tasks",
            base.get("replenishment_overloaded_tasks", 0),
            opt.get("replenishment_overloaded_tasks", 0),
            "tasks",
        ),
    ]
    formatted: list[dict[str, Any]] = []
    for label, b_val, o_val, unit in rows:
        b_f = float(b_val or 0.0)
        o_f = float(o_val or 0.0)
        diff = round(o_f - b_f, 4)
        pct = f"{((o_f - b_f) / b_f) * 100.0:+.2f}%" if abs(b_f) > 1e-9 else "0.00%"
        formatted.append(
            {
                "Operational Metric": label,
                "Baseline Layout": round(b_f, 4),
                "Optimized Layout": round(o_f, 4),
                "Delta (Optimized - Baseline)": diff,
                "Relative Change": pct,
                "Unit": unit,
            }
        )
    return pd.DataFrame(formatted)


def main() -> None:
    client, warehouse_id, warehouses, offline_err = init_page_and_sidebar(
        page_title="08 Scenario Comparison",
        page_icon="⚖️",
    )

    render_page_header(
        title="08 · Baseline vs. Optimized Scenario Benchmarking",
        subtitle="Create, persist, and compare counterfactual warehouse slotting scenarios over identical outbound order cohorts.",
        scope_label=format_scope_badge(warehouse_id),
    )

    if offline_err is not None:
        render_api_unavailable_banner(offline_err, backend_url=client.base_url)
        return

    render_telemetry_disclaimer(
        "Apple-to-Apples Evaluation Guarantee: Every scenario compares the exact pre-optimization baseline slotting snapshot "
        "against the target OptimizationRun across identical customer orders, isolating both pure slotting gains and combined 2-Opt routing savings."
    )

    try:
        runs = client.list_optimization_runs(page=1, page_size=25).get("items", [])
        scenarios_resp = client.list_scenarios(warehouse_id=warehouse_id, page=1, page_size=25)
        scenarios = scenarios_resp.get("items", [])
        if not scenarios and warehouse_id is not None:
            scenarios = client.list_scenarios(page=1, page_size=25).get("items", [])
        sku_total = int(client.list_skus(page=1, page_size=1).get("meta", {}).get("total_items", 0))
    except FrontendAPIError as err:
        render_api_error(err, title="Failed to Load Scenarios or Optimization Runs")
        return

    if sku_total == 0 and not scenarios:
        render_empty_dataset_banner()
        return

    # ------------------------------------------------------------------
    # Section 1: Create & Persist New Scenario Evaluation
    # ------------------------------------------------------------------
    render_section_header("➕ Create & Persist New Scenario Comparison")

    if not runs:
        st.warning(
            "No `OptimizationRun` records exist yet. Execute an optimization run on **04 Slotting Optimizer** first to benchmark a scenario."
        )
    else:
        wh_opts = ["ALL"] + [w["warehouse_id"] for w in warehouses]
        default_wh_idx = wh_opts.index(warehouse_id) if warehouse_id in wh_opts else 0

        with st.container(border=True):
            c1, c2, c3 = st.columns([1.4, 1.2, 1.1])
            with c1:
                target_run_id = st.selectbox(
                    "Target Optimization Run",
                    options=[r["run_id"] for r in runs],
                    format_func=lambda rid: next(
                        (
                            f"{r['run_id']} ({r['algorithm_name']} · Scope={r['warehouse_scope']} · Status={r['run_status']})"
                            for r in runs
                            if r["run_id"] == rid
                        ),
                        rid,
                    ),
                    key="scn_create_run",
                )
            with c2:
                scn_wh = st.selectbox(
                    "Warehouse Evaluation Scope",
                    options=wh_opts,
                    index=default_wh_idx,
                    key="scn_create_wh",
                )
            with c3:
                scn_orders = st.number_input(
                    "Max Orders Cohort (10 – 5,000)",
                    min_value=10,
                    max_value=5000,
                    value=400,
                    step=50,
                    key="scn_create_orders",
                )

            n1, n2, n3 = st.columns([1.4, 2.0, 1.0])
            with n1:
                scn_name = st.text_input(
                    "Scenario Name (optional)",
                    value="",
                    placeholder="e.g., Q4 Peak Slotting Benchmark — WH-001",
                    key="scn_create_name",
                )
            with n2:
                scn_desc = st.text_input(
                    "Scenario Description",
                    value="Counterfactual comparison of baseline slotting vs. constrained optimization.",
                    key="scn_create_desc",
                )
            with n3:
                st.write("")
                create_btn = st.button(
                    "📊 Evaluate & Save Scenario", type="primary", width="stretch", key="btn_create_scn"
                )

            if create_btn:
                with st.spinner(
                    f"Evaluating baseline vs. {target_run_id} across up to {scn_orders} identical orders..."
                ):
                    try:
                        created = client.create_scenario(
                            optimization_run_id=target_run_id,
                            scenario_name=scn_name.strip() or None,
                            description=scn_desc.strip(),
                            warehouse_id=None if scn_wh == "ALL" else scn_wh,
                            max_orders=int(scn_orders),
                        )
                        st.session_state["selected_scenario_id"] = created["scenario_id"]
                        st.success(
                            f"Created and persisted scenario **`{created['scenario_id']}`** (`{created['scenario_name']}`)."
                        )
                        scenarios = client.list_scenarios(page=1, page_size=25).get("items", [])
                    except FrontendAPIError as err:
                        render_api_error(err, title="Scenario Creation Failed")

    # ------------------------------------------------------------------
    # Section 2: Persisted Scenario Inspector & Visual Benchmarking
    # ------------------------------------------------------------------
    st.divider()
    render_section_header("📈 Persisted Scenario Benchmark Explorer")

    if not scenarios:
        st.info("No persisted scenarios exist yet. Use the form above to generate and save a scenario comparison.")
        return

    scn_ids = [s["scenario_id"] for s in scenarios]
    pref_scn = st.session_state.get("selected_scenario_id")
    sel_idx = scn_ids.index(pref_scn) if pref_scn in scn_ids else 0

    chosen_scn_id = st.selectbox(
        "Select Persisted Scenario to Inspect",
        options=scn_ids,
        index=sel_idx,
        format_func=lambda sid: next(
            (
                f"{s['scenario_id']} — {s['scenario_name']} (Run={s['optimization_run_id']} · Scope={s.get('warehouse_id') or 'ALL'})"
                for s in scenarios
                if s["scenario_id"] == sid
            ),
            sid,
        ),
        key="scn_inspector_select",
    )
    st.session_state["selected_scenario_id"] = chosen_scn_id

    try:
        scn_detail = client.get_scenario_comparison(chosen_scn_id)
    except FrontendAPIError as err:
        render_api_error(err, title=f"Failed to Load Scenario {chosen_scn_id}")
        return

    delta = scn_detail.get("comparison_delta_json") or {}
    base_m = scn_detail.get("baseline_metrics_json") or {}
    opt_m = scn_detail.get("optimized_metrics_json") or {}

    d1, d2, d3, d4 = st.columns(4)
    with d1:
        red_pct = float(delta.get("pick_distance_reduction_pct", 0.0))
        saved_m = float(delta.get("pick_distance_saved_m", 0.0))
        render_kpi_card(
            "Pick Distance Reduction",
            f"{red_pct:.2f}%",
            f"Saved {saved_m:,.1f} m across {int(delta.get('orders_compared', 0)):,} identical orders",
            accent="emerald" if red_pct > 0 else "slate",
        )
    with d2:
        pure_m = float(delta.get("pure_slotting_distance_saved_m", 0.0))
        time_pct = float(delta.get("pick_time_improvement_pct", 0.0))
        render_kpi_card(
            "Pure Slotting Relocation Gain",
            f"{pure_m:,.1f} m",
            f"Fulfillment Time Improvement: {time_pct:.2f}% ({float(delta.get('pick_time_saved_seconds', 0.0)) / 60.0:,.1f} min)",
            accent="blue",
        )
    with d3:
        score_d = float(delta.get("average_score_delta", 0.0))
        render_kpi_card(
            "Slotting Quality Score Delta",
            f"{score_d:+.4f}",
            f"Baseline {float(base_m.get('average_assignment_score', 0.0)):.4f} → Optimized {float(opt_m.get('average_assignment_score', 0.0)):.4f}",
            accent="indigo",
        )
    with d4:
        cap_elim = int(delta.get("capacity_violations_eliminated", 0))
        rep_d = float(delta.get("replenishment_labor_delta_minutes", 0.0))
        render_kpi_card(
            "Violations Eliminated",
            f"{cap_elim}",
            f"Replenishment Labor Delta: {rep_d:+.1f} min",
            accent="emerald" if cap_elim >= 0 else "rose",
        )

    fig_travel, fig_quality = build_scenario_comparison_figures(scn_detail)
    ch1, ch2 = st.columns(2)
    with ch1:
        st.plotly_chart(fig_travel, width="stretch")
    with ch2:
        st.plotly_chart(fig_quality, width="stretch")

    st.markdown("#### 📋 Complete Side-by-Side Baseline vs. Optimized KPI Ledger")
    st.dataframe(_build_comparison_table(scn_detail), width="stretch", hide_index=True)


if __name__ == "__main__":
    main()
