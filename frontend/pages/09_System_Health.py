"""Page 09 — System Health, 21-Table Telemetry, Reproducible Data Pipeline & Immutable Audit Trail."""

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
from frontend.components.sidebar import format_scope_badge, init_page_and_sidebar
from frontend.components.theme import (
    render_api_error,
    render_api_unavailable_banner,
    render_kpi_card,
    render_page_header,
    render_section_header,
    status_pill_html,
)


def _build_table_counts_figure(table_counts: dict[str, int]) -> go.Figure:
    sorted_items = sorted(table_counts.items(), key=lambda kv: kv[1])
    names = [k for k, _ in sorted_items]
    counts = [int(v) for _, v in sorted_items]

    fig = go.Figure(
        data=[
            go.Bar(
                x=counts,
                y=names,
                orientation="h",
                marker_color="#1D4ED8",
                text=[f"{c:,}" for c in counts],
                textposition="auto",
            )
        ]
    )
    fig.update_layout(
        title={
            "text": "Persisted Relational Record Counts Across All 21 Operational Tables",
            "font": {"size": 15, "color": "#0F172A"},
        },
        height=490,
        margin={"l": 180, "r": 30, "t": 48, "b": 42},
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#F8FAFC",
        xaxis={"title": "Row Count", "gridcolor": "#E2E8F0"},
        yaxis={"gridcolor": "#E2E8F0"},
    )
    return fig


def main() -> None:
    client, warehouse_id, _warehouses, offline_err = init_page_and_sidebar(
        page_title="09 System Health",
        page_icon="🛡️",
    )

    render_page_header(
        title="09 · System Health, Reproducible Data Pipeline & Immutable Audit Trail",
        subtitle="Inspect live FastAPI & SQLAlchemy liveness, 21-table row counts, SHA-256 dataset manifests, 18-rule data validation, and audit logs.",
        scope_label=format_scope_badge(warehouse_id),
    )

    if offline_err is not None:
        render_api_unavailable_banner(offline_err, backend_url=client.base_url)
        return

    try:
        health = client.get_health()
        sys_status = client.get_system_status()
    except FrontendAPIError as err:
        render_api_error(err, title="System Status Probe Failed")
        return

    # ------------------------------------------------------------------
    # Section 1: Liveness, Database, ML & LangGraph Subsystem Health
    # ------------------------------------------------------------------
    render_section_header("🩺 Live Platform Telemetry & Subsystem Readiness")

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        render_kpi_card(
            "API & Database Status",
            str(sys_status.get("status", "HEALTHY")),
            f"Dialect: {sys_status.get('database_dialect', 'sqlite').upper()} · Connected: {sys_status.get('database_connected', True)}",
            accent="emerald" if sys_status.get("database_connected") else "rose",
        )
    with k2:
        render_kpi_card(
            "Active Dataset Profile",
            str(sys_status.get("active_profile", "small")).upper(),
            f"Seed: {sys_status.get('seed', 20261022)} · {sys_status.get('dataset_version') or 'Unversioned'}",
            accent="blue",
        )
    with k3:
        render_kpi_card(
            "Persisted Relational Rows",
            f"{int(sys_status.get('total_records', 0)):,}",
            f"Across {len(sys_status.get('table_counts', {}))} normalized tables",
            accent="indigo",
        )
    with k4:
        asst = sys_status.get("assistant_status", {})
        ml_st = sys_status.get("ml_status", {})
        render_kpi_card(
            "AI & ML Subsystems",
            "LangGraph Ready" if asst.get("langgraph_enabled") else "Disabled",
            f"Mode: {asst.get('execution_mode', 'N/A')} · ML Artifacts: {'Present' if ml_st.get('trained_artifacts_present') else 'Pending'}",
            accent="emerald",
        )

    st.markdown(
        f"**Service:** `{health.get('service', 'SlotWise AI')} v{sys_status.get('version', '1.0.0')}` &nbsp;|&nbsp; "
        f"**Config Version:** `{sys_status.get('config_version', 'N/A')}` &nbsp;|&nbsp; "
        f"**Environment:** `{health.get('environment', 'local')}` &nbsp;|&nbsp; "
        f"**Write Token Enforced:** `{'Yes (Protected)' if sys_status.get('write_auth_configured') else 'No (Zero-Config Local Mode)'}` &nbsp;|&nbsp; "
        f"**Probe Timestamp (UTC):** `{sys_status.get('timestamp', '')}`"
    )

    table_counts = sys_status.get("table_counts", {})
    if table_counts:
        st.plotly_chart(_build_table_counts_figure(table_counts), width="stretch")

    # ------------------------------------------------------------------
    # Section 2: Reproducible Data Pipeline, Manifests & 18-Rule Validator
    # ------------------------------------------------------------------
    st.divider()
    render_section_header("🧬 Deterministic Data Pipeline, SHA-256 Manifests & 18-Rule Validator")

    tab_manifest, tab_validate, tab_generate, tab_ml = st.tabs(
        [
            "🔐 SHA-256 Reproducibility Manifest",
            "✅ 18-Rule Dataset Integrity Validator",
            "⚙️ Synthetic Dataset Generator (`small` / `medium` / `full`)",
            "📈 ML Forecasting & MLflow Leaderboard",
        ]
    )

    with tab_manifest:
        m_prof = st.selectbox(
            "Inspect Dataset Profile Manifest",
            options=["small", "medium", "full"],
            key="sys_manifest_profile",
        )
        try:
            man_resp = client.get_data_manifest(profile=m_prof)
            if not man_resp.get("available") or not man_resp.get("manifest"):
                st.info(f"No generated manifest found on disk for profile `{m_prof}`.")
            else:
                man = man_resp["manifest"]
                st.markdown(
                    f"**Dataset Version:** `{man.get('dataset_version')}` &nbsp;|&nbsp; "
                    f"**Profile:** `{man.get('profile')}` &nbsp;|&nbsp; "
                    f"**Deterministic Seed:** `{man.get('seed')}` &nbsp;|&nbsp; "
                    f"**Labelled Benchmark Anomalies (Separated):** `{man.get('ground_truth_anomaly_count', 0)}`"
                )
                checksums: dict[str, str] = man.get("file_sha256", {}) or man.get("files", {})
                entity_counts: dict[str, int] = man.get("entity_counts", {})
                if checksums:
                    rows = []
                    for fname, sha_val in sorted(checksums.items()):
                        table_key = fname.replace(".csv", "").replace(".json", "")
                        rows.append(
                            {
                                "Artifact File": fname,
                                "Entity Row Count": entity_counts.get(table_key, "-"),
                                "SHA-256 Checksum": sha_val if isinstance(sha_val, str) else str(sha_val),
                            }
                        )
                    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        except FrontendAPIError as err:
            render_api_error(err, title="Manifest Retrieval Failed")

    with tab_validate:
        v_col1, v_col2 = st.columns([1.2, 2.5])
        with v_col1:
            v_prof = st.selectbox(
                "Profile to Validate",
                options=["small", "medium", "full"],
                key="sys_val_profile",
            )
            run_val_btn = st.button(
                "🔎 Run 18-Rule Dataset Validator", type="primary", width="stretch", key="btn_sys_validate"
            )

        if run_val_btn:
            with st.spinner(
                f"Executing 18 structural, referential, physical, and ground-truth isolation checks on '{v_prof}'..."
            ):
                try:
                    st.session_state["last_validation_report"] = client.validate_data(profile=v_prof)
                except FrontendAPIError as err:
                    render_api_error(err, title="Dataset Validation Failed")

        val_rep: dict[str, Any] | None = st.session_state.get("last_validation_report")
        if val_rep:
            is_ok = bool(val_rep.get("is_valid"))
            summary = val_rep.get("summary", {})
            if is_ok:
                st.success(
                    f"Profile **`{val_rep['profile']}`** passed all 18 validation checks (`is_valid=True`) — "
                    f"Matched `{summary.get('ground_truth_matched_total', 0)}/{summary.get('ground_truth_expected_total', 0)}` separated ground-truth anomalies "
                    f"with `{summary.get('unexpected_error_count', 0)}` unexpected structural errors."
                )
            else:
                st.error(
                    f"Profile **`{val_rep['profile']}`** reported validation failures (`unexpected_error_count={summary.get('unexpected_error_count', 0)}`)."
                )

            checks = val_rep.get("checks", {})
            if checks:
                st.dataframe(
                    pd.DataFrame([{"Validation Rule": k, "Result": v} for k, v in checks.items()]),
                    width="stretch",
                    hide_index=True,
                )

    with tab_generate:
        st.caption(
            "Generate a deterministic synthetic dataset using a fixed RNG seed. Enabling **Load Into Active Database** atomically reloads "
            "the 21 operational tables and runs an initial exception detection scan."
        )
        g1, g2, g3, g4 = st.columns(4)
        with g1:
            g_prof = st.selectbox(
                "Dataset Scale Profile", options=["small", "medium", "full"], key="sys_gen_profile"
            )
        with g2:
            g_seed = st.number_input(
                "Deterministic Seed", min_value=1, max_value=2147483647, value=20261022, step=1, key="sys_gen_seed"
            )
        with g3:
            g_anom = st.checkbox("Inject Benchmark Anomalies", value=True, key="sys_gen_anom")
        with g4:
            g_load = st.checkbox("Load Into Active Database", value=False, key="sys_gen_load")

        confirm_reload = True
        if g_load:
            confirm_reload = st.checkbox(
                "Confirm atomic reload of operational database tables (clears current optimization runs and scenarios)",
                value=False,
                key="sys_gen_confirm",
            )

        if st.button(
            "⚙️ Execute Deterministic Data Generator",
            type="primary",
            disabled=(g_load and not confirm_reload),
            key="btn_sys_generate",
        ):
            with st.spinner(f"Generating '{g_prof}' synthetic warehouse dataset (seed={g_seed})..."):
                try:
                    gen_out = client.generate_data(
                        profile=g_prof,
                        seed=int(g_seed),
                        inject_anomalies=g_anom,
                        load_into_database=g_load,
                    )
                    st.success(
                        f"Generated **`{gen_out['dataset_version']}`** in `{gen_out['elapsed_seconds']:.2f}s` "
                        f"(`{gen_out['ground_truth_anomaly_count']}` separated ground-truth anomalies · Manifest: `{gen_out['manifest_path']}`)."
                    )
                    st.json(gen_out["entity_counts"])
                except FrontendAPIError as err:
                    render_api_error(err, title="Synthetic Data Generation Failed")

    with tab_ml:
        st.caption(
            "Train and evaluate chronological SKU-level demand forecasters comparing Naive Baselines (`NAIVE_LAST_OBSERVATION`, "
            "`NAIVE_MOVING_AVG_7D`) against Scikit-learn regressors (`RIDGE_REGRESSION`, `HIST_GRADIENT_BOOSTING_POISSON`), "
            "logged to local MLflow (`artifacts/mlruns`) with zero future data leakage."
        )
        m1, m2, m3, m4 = st.columns([1.0, 1.0, 1.0, 1.4])
        with m1:
            ml_prof = st.selectbox(
                "Forecast Profile Tag",
                options=["small", "medium", "full"],
                key="sys_ml_profile",
            )
        with m2:
            ml_horizon = st.number_input(
                "Forecast Horizon (Days)",
                min_value=7,
                max_value=30,
                value=14,
                step=1,
                key="sys_ml_horizon",
            )
        with m3:
            ml_ratio = st.slider(
                "Chronological Train Split Ratio",
                min_value=0.65,
                max_value=0.85,
                value=0.80,
                step=0.05,
                key="sys_ml_ratio",
            )
        with m4:
            st.write("")
            train_ml_btn = st.button(
                "🧠 Train & Evaluate ML Forecaster",
                type="primary",
                width="stretch",
                key="btn_sys_train_ml",
            )

        if train_ml_btn:
            with st.spinner("Training Naive vs. ML regressors on chronological split and logging MLflow runs..."):
                try:
                    train_out = client.train_ml_forecaster(
                        profile=ml_prof,
                        forecast_horizon_days=int(ml_horizon),
                        train_ratio=float(ml_ratio),
                    )
                    st.success(
                        f"Trained champion **`{train_out['champion_model_name']}`** — "
                        f"WAPE improvement vs. Naive 7d MA: **`{train_out['wape_improvement_vs_naive_pct']:+.2f}%`**, "
                        f"MAE improvement: **`{train_out['mae_improvement_vs_naive_pct']:+.2f}%`**."
                    )
                except FrontendAPIError as err:
                    render_api_error(err, title="ML Demand Forecaster Training Failed")

        try:
            ml_status_resp = client.get_ml_status(profile=ml_prof)
        except FrontendAPIError as err:
            render_api_error(err, title="ML Status Query Failed")
            ml_status_resp = {}

        eval_sum = ml_status_resp.get("evaluation_summary")
        if not eval_sum:
            st.info(
                f"No trained ML evaluation report found for profile `{ml_prof}` yet. Click **Train & Evaluate ML Forecaster** above."
            )
        else:
            sp = eval_sum.get("split_info", {})
            st.markdown(
                f"**Champion Model:** `{eval_sum.get('champion_model_name')}` &nbsp;|&nbsp; "
                f"**Chronological Split Verified:** `{sp.get('chronological_order_verified')}` "
                f"(Train `{sp.get('train_start_date')}..{sp.get('train_end_date')}` → Test `{sp.get('test_start_date')}..{sp.get('test_end_date')}`) &nbsp;|&nbsp; "
                f"**MLflow Experiment:** `{eval_sum.get('mlflow_experiment_name')}` &nbsp;|&nbsp; "
                f"**Artifact:** `{eval_sum.get('artifact_joblib_path')}`"
            )
            lb = eval_sum.get("leaderboard") or []
            if lb:
                fig_lb = go.Figure(
                    data=[
                        go.Bar(
                            x=[r["model_name"] for r in lb],
                            y=[float(r["daily_wape_pct"]) for r in lb],
                            marker_color=[
                                "#059669"
                                if r.get("is_champion")
                                else ("#2563EB" if r.get("model_family") == "MACHINE_LEARNING" else "#64748B")
                                for r in lb
                            ],
                            text=[f"{float(r['daily_wape_pct']):.2f}%" for r in lb],
                            textposition="auto",
                        )
                    ]
                )
                fig_lb.update_layout(
                    title={
                        "text": "Chronological Holdout Daily WAPE (%) — Naive Baselines vs. ML Models (Lower is Better)",
                        "font": {"size": 15, "color": "#0F172A"},
                    },
                    height=300,
                    margin={"l": 45, "r": 25, "t": 48, "b": 40},
                    paper_bgcolor="#FFFFFF",
                    plot_bgcolor="#F8FAFC",
                    yaxis={"title": "Daily WAPE (%)", "gridcolor": "#E2E8F0"},
                    xaxis={"gridcolor": "#E2E8F0"},
                )
                st.plotly_chart(fig_lb, width="stretch")
                st.dataframe(pd.DataFrame(lb), width="stretch", hide_index=True)

            top_surg = eval_sum.get("top_surging_skus") or []
            if top_surg:
                st.markdown(
                    f"**Top Forecasted Demand Surges (`{eval_sum.get('surging_sku_count', 0)}` Surging SKUs · "
                    f"`{eval_sum.get('velocity_shift_sku_count', 0)}` Velocity Tier Shifts):**"
                )
                st.dataframe(
                    pd.DataFrame(top_surg)[
                        [
                            "sku_id",
                            "sku_name",
                            "historical_daily_demand",
                            "forecast_daily_demand",
                            "surge_ratio",
                            "historical_velocity",
                            "forecast_velocity",
                            "historical_reorder_point",
                            "forecast_reorder_point",
                            "slotting_recommendation",
                            "replenishment_recommendation",
                        ]
                    ],
                    width="stretch",
                    hide_index=True,
                )

    # ------------------------------------------------------------------
    # Section 3: Immutable Operational Audit Trail Explorer
    # ------------------------------------------------------------------
    st.divider()
    render_section_header("📜 Immutable Operational Audit Trail (`audit_events`)")

    a1, a2, a3, a4 = st.columns([1.2, 1.3, 1.3, 0.9])
    with a1:
        aud_entity_type = st.selectbox(
            "Filter by Entity Type",
            options=["ALL", "OPTIMIZATION_RUN", "EXCEPTION", "SCENARIO", "DATASET", "REPLENISHMENT", "ML_MODEL"],
            key="aud_entity_type",
        )
    with a2:
        aud_event_type = st.text_input(
            "Filter by Event Type (optional)",
            value="",
            placeholder="e.g., OPTIMIZATION_EXECUTED",
            key="aud_event_type",
        )
    with a3:
        aud_entity_id = st.text_input(
            "Filter by Entity ID (optional)",
            value="",
            placeholder="e.g., OPT-2026-000001",
            key="aud_entity_id",
        )
    with a4:
        aud_page = st.number_input("Audit Page", min_value=1, value=1, step=1, key="aud_page")

    try:
        audit_resp = client.list_audit_events(
            entity_type=None if aud_entity_type == "ALL" else aud_entity_type,
            entity_id=aud_entity_id.strip() or None,
            event_type=aud_event_type.strip() or None,
            page=int(aud_page),
            page_size=50,
        )
    except FrontendAPIError as err:
        render_api_error(err, title="Audit Trail Query Failed")
        return

    audit_items = audit_resp.get("items", [])
    if not audit_items:
        st.info("No audit log entries matched the selected filters.")
        return

    st.dataframe(
        pd.DataFrame(audit_items)[
            [
                "audit_event_id",
                "created_at",
                "event_type",
                "entity_type",
                "entity_id",
                "actor",
            ]
        ],
        width="stretch",
        hide_index=True,
    )

    selected_aud_id = st.selectbox(
        "Inspect Before / After JSON State Payload for Audit Event",
        options=[a["audit_event_id"] for a in audit_items],
        format_func=lambda aid: next(
            (
                f"{a['audit_event_id']} | {a['event_type']} on {a['entity_type']}:{a['entity_id']} by {a['actor']} ({a['created_at']})"
                for a in audit_items
                if a["audit_event_id"] == aid
            ),
            aid,
        ),
        key="aud_inspect_select",
    )
    selected_aud = next(a for a in audit_items if a["audit_event_id"] == selected_aud_id)

    st.markdown(
        f"**Audit Event:** `{selected_aud['audit_event_id']}` &nbsp;|&nbsp; "
        f"**Action:** {status_pill_html(selected_aud['event_type'])} &nbsp;|&nbsp; "
        f"**Actor:** `{selected_aud['actor']}`",
        unsafe_allow_html=True,
    )
    j1, j2, j3 = st.columns(3)
    with j1:
        st.caption("Previous State (`previous_state_json`)")
        st.json(selected_aud.get("previous_state_json") or {})
    with j2:
        st.caption("New State (`new_state_json`)")
        st.json(selected_aud.get("new_state_json") or {})
    with j3:
        st.caption("Metadata (`metadata_json`)")
        st.json(selected_aud.get("metadata_json") or {})


if __name__ == "__main__":
    main()
