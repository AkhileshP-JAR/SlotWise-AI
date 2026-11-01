"""Page 04 — Constrained Slotting Optimization Engine, Governance Approval & Decision Explanations."""

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
from frontend.components.charts import (
    build_candidate_score_comparison_figure,
    build_rejection_reasons_figure,
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


def _build_active_score_components_chart(comp: dict[str, float]) -> go.Figure:
    labels = [
        "Total Score",
        "Velocity (0.30)",
        "Pick Freq (0.20)",
        "Distance (0.15)",
        "Capacity (0.15)",
        "Replenishment (0.10)",
        "Compatibility (0.10)",
    ]
    values = [
        float(comp.get("assignment_score", 0.0)),
        float(comp.get("velocity_score", 0.0)),
        float(comp.get("pick_frequency_score", 0.0)),
        float(comp.get("distance_score", 0.0)),
        float(comp.get("capacity_score", 0.0)),
        float(comp.get("replenishment_score", 0.0)),
        float(comp.get("compatibility_score", 0.0)),
    ]
    colors = ["#0F172A", "#1D4ED8", "#2563EB", "#059669", "#10B981", "#D97706", "#6366F1"]
    fig = go.Figure(
        data=[
            go.Bar(
                x=labels,
                y=values,
                marker_color=colors,
                text=[f"{v:.3f}" for v in values],
                textposition="auto",
            )
        ]
    )
    fig.update_layout(
        title={
            "text": "Average Active Assignment 6-Factor Score Profile [0.0 – 1.0]",
            "font": {"size": 15, "color": "#0F172A"},
        },
        height=310,
        margin={"l": 45, "r": 25, "t": 48, "b": 42},
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#F8FAFC",
        yaxis={"range": [0, 1.08], "title": "Normalized Score", "gridcolor": "#E2E8F0"},
        xaxis={"gridcolor": "#E2E8F0"},
    )
    return fig


def main() -> None:
    client, warehouse_id, warehouses, offline_err = init_page_and_sidebar(
        page_title="04 Slotting Optimizer",
        page_icon="⚙️",
    )

    render_page_header(
        title="04 · Constrained Slotting Optimization & Governance Workbench",
        subtitle="Execute OR-Tools CP-SAT or deterministic Greedy+2-Swap slotting, inspect unassigned SKU failures, and govern PROPOSED → ACTIVE transitions.",
        scope_label=format_scope_badge(warehouse_id),
    )

    if offline_err is not None:
        render_api_unavailable_banner(offline_err, backend_url=client.base_url)
        return

    try:
        sku_probe = client.list_skus(warehouse_id=None, page=1, page_size=1)
        total_skus_in_db = int(sku_probe.get("meta", {}).get("total_items", 0))
    except FrontendAPIError as err:
        render_api_error(err, title="Failed to Query Operational Catalog")
        return

    if total_skus_in_db == 0:
        render_empty_dataset_banner()
        return

    # ------------------------------------------------------------------
    # Section 1: Optimization Execution Controls
    # ------------------------------------------------------------------
    render_section_header("🚀 Execute Constrained Slotting Optimization Run")

    wh_opts = ["ALL"] + [w["warehouse_id"] for w in warehouses]
    default_wh_idx = wh_opts.index(warehouse_id) if warehouse_id in wh_opts else 0

    with st.container(border=True):
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            target_wh = st.selectbox(
                "Target Warehouse Scope",
                options=wh_opts,
                index=default_wh_idx,
                key="opt_target_wh",
            )
        with c2:
            zone_options = ["ALL"]
            if target_wh != "ALL":
                try:
                    wh_det = client.get_warehouse(target_wh)
                    zone_options += [z["zone_id"] for z in wh_det.get("zones", [])]
                except FrontendAPIError:
                    pass
            target_zone = st.selectbox("Optional Zone Filter", options=zone_options, key="opt_target_zone")
        with c3:
            strategy = st.selectbox(
                "Optimization Solver Strategy",
                options=["auto", "ortools_cpsat", "greedy"],
                format_func=lambda s: {
                    "auto": "Auto (CP-SAT ≤ 1,500 SKUs, Greedy fallback)",
                    "ortools_cpsat": "Google OR-Tools CP-SAT Exact Solver",
                    "greedy": "Deterministic Greedy + Local 2-Swap",
                }[s],
                key="opt_strategy",
            )
        with c4:
            policy_override = st.selectbox(
                "Storage Policy Override",
                options=["DEFAULT", "DEDICATED_SINGLE_SKU", "SHARED_COMPATIBLE"],
                key="opt_policy_override",
            )

        o1, o2, o3, o4 = st.columns([2.0, 1.1, 1.1, 1.1])
        with o1:
            sku_subset_raw = st.text_input(
                "Optional Explicit SKU Subset (comma-separated SKU IDs; leave blank for full scope)",
                value="",
                placeholder="e.g., SKU-000001, SKU-000002, SKU-000209",
                key="opt_sku_subset",
            )
        with o2:
            preserve_affinity = st.checkbox(
                "Preserve Regional Warehouse Affinity",
                value=True,
                key="opt_preserve_affinity",
            )
        with o3:
            apply_immediately = st.checkbox(
                "Apply Immediately (Skip PROPOSED Review)",
                value=False,
                key="opt_apply_immediately",
            )
        with o4:
            use_ml_forecasts = st.checkbox(
                "Use ML Demand Forecasts (Forecast-Guided)",
                value=False,
                key="opt_use_ml_forecasts",
            )

        if st.button("⚡ Run Constrained Slotting Optimizer", type="primary", key="btn_exec_opt"):
            parsed_skus = [s.strip() for s in sku_subset_raw.split(",") if s.strip()] or None
            with st.spinner(
                "Evaluating hard constraints, scoring feasible candidate pairs, and solving slotting model..."
            ):
                try:
                    new_run = client.run_optimization(
                        warehouse_id=None if target_wh == "ALL" else target_wh,
                        zone_id=None if target_zone == "ALL" else target_zone,
                        sku_ids=parsed_skus,
                        strategy=strategy,
                        storage_policy_override=None if policy_override == "DEFAULT" else policy_override,
                        preserve_warehouse_affinity=preserve_affinity,
                        apply_immediately=apply_immediately,
                        use_ml_forecasts=use_ml_forecasts,
                    )
                    st.session_state["selected_opt_run_id"] = new_run["run_id"]
                    st.session_state["last_opt_run_banner"] = new_run
                except FrontendAPIError as err:
                    render_api_error(err, title="Slotting Optimization Execution Error")

    last_banner: dict[str, Any] | None = st.session_state.get("last_opt_run_banner")
    if last_banner:
        r_stat = last_banner.get("run_status", "COMPLETED")
        msg = (
            f"Run **`{last_banner['run_id']}`** finished with status **`{r_stat}`** using **`{last_banner['algorithm_name']}`** "
            f"in `{last_banner['execution_duration_ms']:.1f} ms` — Assigned `{last_banner['assignment_count']}/{last_banner['sku_count']}` SKUs "
            f"(`{last_banner['unassigned_sku_count']}` unassigned, `{last_banner['candidate_count']:,}` feasible candidates evaluated)."
        )
        if r_stat == "FAILED":
            st.error(f"**Optimization Failure (`FAILED`)** — {msg}")
        elif r_stat == "PARTIAL" or int(last_banner.get("unassigned_sku_count", 0)) > 0:
            st.warning(f"**Partial Optimization (`PARTIAL` — Hard Constraints Enforced)** — {msg}")
        else:
            st.success(f"**Optimization Completed (`COMPLETED`)** — {msg}")

    # ------------------------------------------------------------------
    # Section 2: Optimization Analytics & Run Governance
    # ------------------------------------------------------------------
    try:
        opt_analytics = client.get_optimization_analytics()
        runs_resp = client.list_optimization_runs(
            warehouse_scope=warehouse_id,
            page=1,
            page_size=25,
        )
        # Fall back to global run list if scoped list is empty so enterprise runs remain inspectable
        runs = runs_resp.get("items", [])
        if not runs and warehouse_id is not None:
            runs = client.list_optimization_runs(page=1, page_size=25).get("items", [])
    except FrontendAPIError as err:
        render_api_error(err, title="Failed to Load Optimization Run History")
        return

    st.divider()
    render_section_header("📊 Optimization Performance & Assignment Lifecycle Summary")

    status_counts = opt_analytics.get("assignment_status_counts", {})
    latest_run = opt_analytics.get("latest_run") or {}
    latest_kpis = latest_run.get("kpi_results_json") or {}

    k1, k2, k3, k4, k5 = st.columns(5)
    with k1:
        render_kpi_card(
            "Total Optimization Runs",
            f"{opt_analytics.get('total_runs', 0)}",
            f"Latest: {latest_run.get('run_id', 'None')} ({latest_run.get('algorithm_name', 'N/A')})",
            accent="blue",
        )
    with k2:
        succ_pct = float(latest_kpis.get("assignment_success_rate", 0.0)) * 100.0
        render_kpi_card(
            "Latest Run Success Rate",
            f"{succ_pct:.1f}%",
            f"Status: {latest_run.get('run_status', 'N/A')} · Violations: {latest_kpis.get('constraint_violation_count', 0)}",
            accent="emerald" if succ_pct >= 95.0 else "amber",
        )
    with k3:
        render_kpi_card(
            "Active Assignments",
            f"{status_counts.get('ACTIVE', 0):,}",
            f"Avg Score: {float(opt_analytics.get('average_active_score_components', {}).get('assignment_score', 0.0)):.4f}",
            accent="indigo",
        )
    with k4:
        render_kpi_card(
            "Proposed (Pending Review)",
            f"{status_counts.get('PROPOSED', 0):,}",
            "Awaiting operator approval/rejection",
            accent="amber" if int(status_counts.get("PROPOSED", 0)) > 0 else "slate",
        )
    with k5:
        render_kpi_card(
            "Superseded & Rejected",
            f"{int(status_counts.get('SUPERSEDED', 0)) + int(status_counts.get('REJECTED', 0)):,}",
            f"Superseded: {status_counts.get('SUPERSEDED', 0)} · Rejected: {status_counts.get('REJECTED', 0)}",
            accent="slate",
        )

    avg_comp = opt_analytics.get("average_active_score_components", {})
    if avg_comp:
        st.plotly_chart(_build_active_score_components_chart(avg_comp), width="stretch")

    if not runs:
        st.info(
            "No optimization runs have been executed yet. Use the form above to trigger your first optimization run."
        )
        return

    st.divider()
    render_section_header("🏛️ Run Explorer, Unassigned Failure Diagnostics & Governance Actions")

    run_ids = [r["run_id"] for r in runs]
    pref_run = st.session_state.get("selected_opt_run_id")
    sel_idx = run_ids.index(pref_run) if pref_run in run_ids else 0

    selected_run_id = st.selectbox(
        "Select Optimization Run to Inspect or Govern",
        options=run_ids,
        index=sel_idx,
        format_func=lambda rid: next(
            (
                f"{r['run_id']} | Scope={r['warehouse_scope']} | {r['algorithm_name']} | Status={r['run_status']} | "
                f"Assigned {r['assignment_count']}/{r['sku_count']} ({r['execution_duration_ms']:.0f} ms)"
                for r in runs
                if r["run_id"] == rid
            ),
            rid,
        ),
        key="opt_run_selector",
    )
    st.session_state["selected_opt_run_id"] = selected_run_id
    selected_run = next(r for r in runs if r["run_id"] == selected_run_id)
    run_kpis = selected_run.get("kpi_results_json") or {}

    st.markdown(
        f"**Selected Run:** `{selected_run['run_id']}` &nbsp;|&nbsp; "
        f"**Status:** {status_pill_html(selected_run['run_status'])} &nbsp;|&nbsp; "
        f"**Algorithm:** `{selected_run['algorithm_name']} v{selected_run['algorithm_version']}` &nbsp;|&nbsp; "
        f"**Dataset:** `{selected_run['input_dataset_version']}` &nbsp;|&nbsp; "
        f"**Avg Score:** `{float(run_kpis.get('average_assignment_score', 0.0)):.4f}` &nbsp;|&nbsp; "
        f"**Hard-Constraint Violations:** `{run_kpis.get('constraint_violation_count', 0)}`",
        unsafe_allow_html=True,
    )

    # Unassigned SKUs / Optimization failure diagnostics
    unassigned_skus: list[str] = list(run_kpis.get("unassigned_skus") or [])
    if unassigned_skus:
        with st.expander(
            f"⚠️ Inspect {len(unassigned_skus)} Unassigned SKU(s) Blocked by Hard Physical/Safety Constraints ({', '.join(unassigned_skus)})",
            expanded=True,
        ):
            st.caption(
                "SlotWise AI never overrides hard physical, thermal, hazard, or capacity rules with soft scoring weights. "
                "Inspect the exact constraint rejection breakdown for any unassigned SKU below:"
            )
            u_sku = st.selectbox("Select Unassigned SKU", options=unassigned_skus, key="opt_unassigned_sku_select")
            try:
                u_cand = client.get_sku_candidates(
                    sku_id=u_sku,
                    warehouse_id=None
                    if selected_run["warehouse_scope"] == "ALL"
                    else selected_run["warehouse_scope"],
                    top_n=5,
                    max_rejected_samples=10,
                )
                rej_map = u_cand.get("rejection_reason_counts", {})
                if rej_map:
                    st.plotly_chart(build_rejection_reasons_figure(rej_map), width="stretch")
                if u_cand.get("rejected_samples"):
                    st.dataframe(
                        pd.DataFrame(
                            [
                                {
                                    "location_id": s["location_id"],
                                    "warehouse_id": s["warehouse_id"],
                                    "zone_type": s["zone_type"],
                                    "rejection_reasons": ", ".join(s["rejection_reasons"]),
                                    "explanation": s["score_breakdown"]["explanation"],
                                }
                                for s in u_cand["rejected_samples"]
                            ]
                        ),
                        width="stretch",
                        hide_index=True,
                    )
            except FrontendAPIError as err:
                render_api_error(err, title=f"Could Not Inspect Unassigned SKU {u_sku}")

    # Governance Approval / Rejection controls
    g1, g2, g3 = st.columns([1.2, 1.8, 1.2])
    with g1:
        auto_res = st.checkbox(
            "Auto-resolve remediated slotting exceptions on approval",
            value=True,
            key="opt_approve_autoresolve",
        )
        if st.button("✅ Approve PROPOSED Assignments", width="stretch", key="btn_approve_run"):
            with st.spinner(
                f"Promoting PROPOSED assignments in {selected_run_id} to ACTIVE and superseding prior slots..."
            ):
                try:
                    app_res = client.approve_run_assignments(
                        run_id=selected_run_id,
                        auto_resolve_exceptions=auto_res,
                    )
                    if int(app_res.get("approved_count", 0)) > 0:
                        st.success(
                            f"Approved **{app_res['approved_count']}** assignments in `{selected_run_id}` "
                            f"(Superseded `{app_res['superseded_count']}` prior assignments across `{app_res['reconciled_locations']}` locations)."
                        )
                    else:
                        st.info(
                            f"Run `{selected_run_id}` currently has 0 `PROPOSED` assignments awaiting approval."
                        )
                except FrontendAPIError as err:
                    render_api_error(err, title="Approval Governance Action Failed")
    with g2:
        reject_reason = st.text_input(
            "Rejection Audit Note (used when rejecting PROPOSED assignments)",
            value="Operator rejected proposed re-slotting during workbench review.",
            key="opt_reject_reason",
        )
    with g3:
        st.write("")
        if st.button("🚫 Reject PROPOSED Assignments", width="stretch", key="btn_reject_run"):
            with st.spinner(f"Rejecting PROPOSED recommendations for {selected_run_id}..."):
                try:
                    rej_res = client.reject_run_assignments(
                        run_id=selected_run_id,
                        reason=reject_reason,
                    )
                    if int(rej_res.get("rejected_count", 0)) > 0:
                        st.warning(
                            f"Rejected **{rej_res['rejected_count']}** `PROPOSED` assignments in `{selected_run_id}`. Existing `ACTIVE` slots remain untouched."
                        )
                    else:
                        st.info(f"Run `{selected_run_id}` has 0 `PROPOSED` assignments to reject.")
                except FrontendAPIError as err:
                    render_api_error(err, title="Rejection Governance Action Failed")

    # ------------------------------------------------------------------
    # Section 3: Assignment Decision Explanations & Non-Destructive History
    # ------------------------------------------------------------------
    tab_run_asgn, tab_lineage = st.tabs(
        [
            f"🔍 Run {selected_run_id} Assignments & Candidate Explanations",
            "📜 Non-Destructive Assignment Lineage (ACTIVE / PROPOSED / SUPERSEDED / REJECTED)",
        ]
    )

    with tab_run_asgn:
        af1, af2 = st.columns([1.2, 1.5])
        with af1:
            asgn_status_filter = st.selectbox(
                "Filter Run Assignments by Status",
                options=["ALL", "ACTIVE", "PROPOSED", "SUPERSEDED", "REJECTED"],
                key="opt_asgn_status_filter",
            )
        with af2:
            asgn_sku_search = st.text_input(
                "Filter by Exact SKU ID (optional)",
                value="",
                placeholder="e.g., SKU-000001",
                key="opt_asgn_sku_filter",
            )

        try:
            asgn_resp = client.list_run_assignments(
                run_id=selected_run_id,
                warehouse_id=warehouse_id,
                sku_id=asgn_sku_search.strip() or None,
                assignment_status=None if asgn_status_filter == "ALL" else asgn_status_filter,
                include_reasons=True,
                page=1,
                page_size=100,
            )
        except FrontendAPIError as err:
            render_api_error(err, title="Failed to Load Run Assignments")
            return

        asgn_items = asgn_resp.get("items", [])
        if not asgn_items:
            st.info("No assignments in this run matched the active status / SKU filter.")
        else:
            st.dataframe(
                pd.DataFrame(asgn_items)[
                    [
                        "slot_assignment_id",
                        "sku_id",
                        "warehouse_id",
                        "location_id",
                        "assignment_status",
                        "assigned_quantity",
                        "assignment_score",
                        "velocity_score",
                        "pick_frequency_score",
                        "distance_score",
                        "capacity_score",
                        "decision_version",
                    ]
                ],
                width="stretch",
                hide_index=True,
            )

            chosen_asgn_id = st.selectbox(
                "Select Assignment to Inspect Decision Explanation & Candidate Comparison",
                options=[a["slot_assignment_id"] for a in asgn_items],
                format_func=lambda aid: next(
                    (
                        f"{a['slot_assignment_id']} | {a['sku_id']} → {a['location_id']} ({a['warehouse_id']}) | "
                        f"Status={a['assignment_status']} | Score={a['assignment_score']:.4f}"
                        for a in asgn_items
                        if a["slot_assignment_id"] == aid
                    ),
                    aid,
                ),
                key="opt_inspect_asgn_id",
            )
            chosen_asgn = next(a for a in asgn_items if a["slot_assignment_id"] == chosen_asgn_id)

            st.info(
                f"**Decision Rationale (`v{chosen_asgn['decision_version']}`):** {chosen_asgn['decision_reason']}"
            )

            reasons = chosen_asgn.get("candidate_reasons") or []
            feas_reasons = [
                {
                    "rank_position": r.get("rank_position") or idx + 1,
                    "location_id": r["candidate_location_id"],
                    "zone_type": "SELECTED" if r.get("is_selected") else "ALTERNATIVE",
                    "total_score": r["total_score"],
                    "score_breakdown": r.get("score_breakdown_json") or {},
                }
                for idx, r in enumerate(reasons)
                if r.get("is_feasible")
            ]
            if feas_reasons:
                st.plotly_chart(build_candidate_score_comparison_figure(feas_reasons), width="stretch")

            if reasons:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "candidate_location_id": r["candidate_location_id"],
                                "is_selected": r["is_selected"],
                                "is_feasible": r["is_feasible"],
                                "rank": r["rank_position"],
                                "total_score": r["total_score"],
                                "rejection_reasons": ", ".join(r.get("rejection_reasons_json") or [])
                                or "PASS (All 10 Hard Constraints)",
                            }
                            for r in reasons
                        ]
                    ),
                    width="stretch",
                    hide_index=True,
                )

    with tab_lineage:
        lh1, lh2 = st.columns(2)
        with lh1:
            hist_status = st.selectbox(
                "Lifecycle Status Filter",
                options=["ALL", "ACTIVE", "SUPERSEDED", "PROPOSED", "REJECTED"],
                key="opt_hist_status",
            )
        with lh2:
            hist_sku = st.text_input(
                "Filter Lineage by SKU ID (optional)",
                value="",
                placeholder="e.g., SKU-000001",
                key="opt_hist_sku",
            )
        try:
            hist_resp = client.list_assignment_history(
                warehouse_id=warehouse_id,
                sku_id=hist_sku.strip() or None,
                assignment_status=None if hist_status == "ALL" else hist_status,
                page=1,
                page_size=100,
            )
            hist_items = hist_resp.get("items", [])
            if hist_items:
                st.dataframe(
                    pd.DataFrame(hist_items)[
                        [
                            "slot_assignment_id",
                            "optimization_run_id",
                            "sku_id",
                            "warehouse_id",
                            "location_id",
                            "assignment_status",
                            "decision_version",
                            "assignment_score",
                            "effective_from",
                            "effective_to",
                            "decision_reason",
                        ]
                    ],
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.info("No assignment history records matched the selected filter.")
        except FrontendAPIError as err:
            render_api_error(err, title="Assignment History Query Failed")


if __name__ == "__main__":
    main()
