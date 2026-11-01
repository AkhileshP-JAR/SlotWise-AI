"""Page 07 — Operational Exception Detection Center, F1 Benchmark Audit & Lifecycle State Machine."""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pandas as pd
import streamlit as st

from frontend.components.api_client import FrontendAPIError
from frontend.components.charts import build_exceptions_summary_figures
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
        page_title="07 Exception Center",
        page_icon="🚨",
    )

    render_page_header(
        title="07 · Operational Exception Detection & Lifecycle Resolution Center",
        subtitle="Scan live warehouse tables for safety, thermal, capacity, and data-quality violations (zero ground-truth access) and govern lifecycle transitions.",
        scope_label=format_scope_badge(warehouse_id),
    )

    if offline_err is not None:
        render_api_unavailable_banner(offline_err, backend_url=client.base_url)
        return

    try:
        overview = client.get_analytics_overview(warehouse_id=warehouse_id, sample_picking_orders=50)
    except FrontendAPIError as err:
        render_api_error(err, title="Failed to Load Exception KPIs")
        return

    wh_kpis = overview.get("warehouse", {})
    sku_kpis = overview.get("sku", {})
    exc_kpis = overview.get("exceptions", {})
    gt_eval = overview.get("ground_truth_evaluation", {})

    if (
        int(wh_kpis.get("total_locations", 0)) == 0
        and int(sku_kpis.get("total_skus", 0)) == 0
        and int(exc_kpis.get("total_exceptions", 0)) == 0
    ):
        render_empty_dataset_banner()
        return

    # ------------------------------------------------------------------
    # Top KPI Row + Live Operational Exception Scan Trigger
    # ------------------------------------------------------------------
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        render_kpi_card(
            "Total Recorded Exceptions",
            f"{int(exc_kpis.get('total_exceptions', 0)):,}",
            f"Resolved: {int(exc_kpis.get('resolved_exceptions', 0)):,}",
            accent="blue",
        )
    with k2:
        open_cnt = int(exc_kpis.get("open_exceptions", 0))
        render_kpi_card(
            "Active Open / In-Review",
            f"{open_cnt:,}",
            f"Critical & High Open: {int(exc_kpis.get('critical_and_high_open', 0)):,}",
            accent="rose" if open_cnt > 0 else "emerald",
        )
    with k3:
        render_kpi_card(
            "Warning & Low Open",
            f"{int(exc_kpis.get('warning_and_low_open', 0)):,}",
            "Operational & data-quality alerts",
            accent="amber",
        )
    with k4:
        if gt_eval.get("ground_truth_available"):
            f1 = float(gt_eval.get("f1_score", 0.0))
            render_kpi_card(
                "Benchmark Detection F1",
                f"{f1:.3f}",
                f"TP={gt_eval.get('true_positives', 0)} · FP={gt_eval.get('false_positives', 0)} · FN={gt_eval.get('false_negatives', 0)}",
                accent="emerald" if f1 >= 0.99 else "amber",
            )
        else:
            render_kpi_card("Benchmark Detection F1", "N/A", "No read-only ground truth file", accent="slate")

    sc1, sc2, sc3 = st.columns([1.4, 1.5, 2.0])
    with sc1:
        auto_res_toggle = st.checkbox(
            "Auto-resolve remediated slotting violations",
            value=True,
            key="exc_scan_autoresolve",
        )
    with sc2:
        if st.button(
            "🔎 Run Live Operational Exception Scan", type="primary", width="stretch", key="btn_exc_scan"
        ):
            with st.spinner(
                "Scanning live relational tables for 10+ safety, capacity, aisle, and data-quality rules..."
            ):
                try:
                    scan_res = client.scan_exceptions(auto_resolve_remediated=auto_res_toggle)
                    st.success(
                        f"Scan complete — **`{scan_res['active_findings_count']}`** active findings, "
                        f"**`{scan_res['newly_created_count']}`** newly recorded, "
                        f"**`{scan_res['auto_resolved_count']}`** remediated & auto-resolved."
                    )
                except FrontendAPIError as err:
                    render_api_error(err, title="Exception Detection Scan Failed")
    with sc3:
        show_gt_col = st.checkbox(
            "Attach read-only Ground-Truth Evaluation Badge (presentation only)",
            value=True,
            key="exc_include_gt",
        )

    # ------------------------------------------------------------------
    # Multi-Attribute Exception Filter Bar
    # ------------------------------------------------------------------
    st.divider()
    render_section_header("🎛️ Filter & Inspect Exception Records")

    f1, f2, f3, f4, f5 = st.columns([1.2, 1.4, 1.0, 1.0, 1.1])
    with f1:
        cat_filter = st.selectbox(
            "Category",
            options=[
                "ALL",
                "BUSINESS_RULE_VIOLATION",
                "OPERATIONAL_WARNING",
                "DATA_QUALITY_VIOLATION",
                "OPTIMIZATION_FAILURE",
            ],
            key="exc_cat_filter",
        )
    with f2:
        issue_filter = st.selectbox(
            "Issue Type",
            options=[
                "ALL",
                "CAPACITY_EXCEEDED",
                "INCOMPATIBLE_COLOCATION",
                "TEMPERATURE_VIOLATION",
                "HAZARD_ZONE_VIOLATION",
                "BLOCKED_AISLE_PICK_PATH",
                "UNASSIGNABLE_OVERSIZED_SKU",
                "INVALID_SKU_DIMENSIONS",
                "MISSING_SUPPLIER_REFERENCE",
                "DUPLICATE_SKU_METADATA",
                "NEGATIVE_PICK_DURATION",
                "REPLENISHMENT_LABOR_OVERLOAD",
            ],
            key="exc_issue_filter",
        )
    with f3:
        sev_filter = st.selectbox(
            "Severity",
            options=["ALL", "CRITICAL", "HIGH", "MEDIUM", "LOW"],
            key="exc_sev_filter",
        )
    with f4:
        res_filter = st.selectbox(
            "Lifecycle Status",
            options=["ALL", "OPEN", "IN_REVIEW", "RESOLVED", "DISMISSED"],
            key="exc_res_filter",
        )
    with f5:
        entity_search = st.text_input(
            "Exact Entity ID",
            value="",
            placeholder="e.g., SKU-000209",
            key="exc_entity_search",
        )

    try:
        exc_page = client.list_exceptions(
            category=None if cat_filter == "ALL" else cat_filter,
            issue_type=None if issue_filter == "ALL" else issue_filter,
            severity=None if sev_filter == "ALL" else sev_filter,
            resolution_status=None if res_filter == "ALL" else res_filter,
            entity_id=entity_search.strip() or None,
            include_ground_truth_eval=show_gt_col,
            page=1,
            page_size=150,
        )
    except FrontendAPIError as err:
        render_api_error(err, title="Exception List Query Failed")
        return

    exceptions = exc_page.get("items", [])
    if not exceptions:
        st.info("No exception records matched the current filter criteria.")
        return

    fig_issues, fig_status = build_exceptions_summary_figures(exceptions)
    ch1, ch2 = st.columns([1.45, 1.0])
    with ch1:
        st.plotly_chart(fig_issues, width="stretch")
    with ch2:
        st.plotly_chart(fig_status, width="stretch")

    display_cols = [
        "exception_id",
        "severity",
        "resolution_status",
        "category",
        "issue_type",
        "entity_type",
        "entity_id",
        "description",
        "recommended_action",
    ]
    if show_gt_col:
        display_cols.append("ground_truth_flag")

    st.dataframe(pd.DataFrame(exceptions)[display_cols], width="stretch", hide_index=True)

    # ------------------------------------------------------------------
    # Interactive Exception State-Machine Resolution Panel
    # ------------------------------------------------------------------
    st.divider()
    render_section_header(
        "🛠️ Exception Lifecycle State-Machine Resolution (`OPEN` ↔ `IN_REVIEW` ↔ `RESOLVED` / `DISMISSED`)"
    )
    st.caption(
        "Allowed state transitions enforced by the backend domain service: "
        "`OPEN → {IN_REVIEW, RESOLVED, DISMISSED}`, `IN_REVIEW → {OPEN, RESOLVED, DISMISSED}`, "
        "`RESOLVED → {OPEN, IN_REVIEW}`, `DISMISSED → {OPEN, IN_REVIEW}`. Invalid transitions return a structured `400 DOMAIN_VALIDATION_ERROR`."
    )

    selected_exc_id = st.selectbox(
        "Select Exception Record to Inspect or Transition",
        options=[e["exception_id"] for e in exceptions],
        format_func=lambda eid: next(
            (
                f"{e['exception_id']} [{e['severity']} · {e['resolution_status']}] — {e['issue_type']} on {e['entity_type']} {e['entity_id']}"
                for e in exceptions
                if e["exception_id"] == eid
            ),
            eid,
        ),
        key="exc_resolve_selector",
    )
    selected_exc = next(e for e in exceptions if e["exception_id"] == selected_exc_id)

    with st.container(border=True):
        st.markdown(
            f"**Exception:** `{selected_exc['exception_id']}` &nbsp;|&nbsp; "
            f"**Severity:** {status_pill_html(selected_exc['severity'])} &nbsp;|&nbsp; "
            f"**Current Status:** {status_pill_html(selected_exc['resolution_status'])} &nbsp;|&nbsp; "
            f"**Entity:** `{selected_exc['entity_type']} · {selected_exc['entity_id']}` &nbsp;|&nbsp; "
            f"**Related Entities:** `{', '.join(selected_exc.get('related_entity_ids_json') or []) or 'None'}`",
            unsafe_allow_html=True,
        )
        st.markdown(f"**Finding:** {selected_exc['description']}")
        st.markdown(f"**Recommended Remediation:** {selected_exc['recommended_action']}")
        if selected_exc.get("resolution_note"):
            st.caption(
                f"Last Resolution Note ({selected_exc.get('resolved_at') or 'Recorded'}): {selected_exc['resolution_note']}"
            )

        r_col1, r_col2, r_col3 = st.columns([1.1, 2.2, 1.0])
        with r_col1:
            next_status = st.selectbox(
                "Target Lifecycle Status",
                options=["IN_REVIEW", "RESOLVED", "DISMISSED", "OPEN"],
                key="exc_target_status",
            )
        with r_col2:
            res_note = st.text_input(
                "Operator Resolution / Audit Note",
                value=f"Reviewed via SlotWise AI Exception Center ({selected_exc['issue_type']}).",
                key="exc_res_note",
            )
        with r_col3:
            st.write("")
            if st.button(
                "💾 Apply Status Transition", type="primary", width="stretch", key="btn_apply_exc_transition"
            ):
                with st.spinner(
                    f"Applying lifecycle transition {selected_exc['resolution_status']} → {next_status}..."
                ):
                    try:
                        updated = client.resolve_exception(
                            exception_id=selected_exc_id,
                            resolution_status=next_status,
                            resolution_note=res_note.strip() or None,
                        )
                        st.success(
                            f"Updated **`{updated['exception_id']}`** to **`{updated['resolution_status']}`** and wrote an immutable `AuditEvent`."
                        )
                    except FrontendAPIError as err:
                        render_api_error(err, title="Lifecycle State Transition Rejected")


if __name__ == "__main__":
    main()
