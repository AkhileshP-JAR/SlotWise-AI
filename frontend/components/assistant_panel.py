"""Reusable LangGraph Warehouse Decision Assistant UI widget."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from frontend.components.api_client import FrontendAPIError, SlotWiseAPIClient
from frontend.components.theme import render_api_error, render_section_header, status_pill_html

SAMPLE_QUESTIONS: list[str] = [
    "What are the picking distance savings across identical orders?",
    "Which SKUs have surging ML demand forecasts and what is our forecast WAPE?",
    "Which fast-moving SKUs are top relocation candidates?",
    "Why was SKU-000001 placed or rejected across candidate slots?",
    "Summarize the top open operational exceptions and recommended fixes.",
    "Which warehouse has the highest replenishment labor utilization?",
]


def render_decision_assistant_panel(
    client: SlotWiseAPIClient,
    warehouse_id: str | None = None,
    key_prefix: str = "asst",
) -> None:
    """Render an interactive grounded LangGraph Decision Assistant query console."""
    render_section_header("🤖 Grounded LangGraph Warehouse Decision Assistant")
    st.caption(
        "Executes a 6-node deterministic + SQL-grounded LangGraph analysis workflow (`POST /api/v1/assistant/query`). "
        "Every cited entity ID is verified against relational database records."
    )

    state_q_key = f"{key_prefix}_question_text"
    state_res_key = f"{key_prefix}_last_response"

    if state_q_key not in st.session_state:
        st.session_state[state_q_key] = SAMPLE_QUESTIONS[0]

    preset_col, custom_col = st.columns([1, 2])
    with preset_col:
        chosen_preset = st.selectbox(
            "Sample Operational Prompts",
            options=SAMPLE_QUESTIONS,
            key=f"{key_prefix}_preset_select",
        )
        if st.button("Load Sample Prompt", key=f"{key_prefix}_btn_load_preset", width="stretch"):
            st.session_state[state_q_key] = chosen_preset
            st.rerun()

    with custom_col:
        question = st.text_input(
            "Ask an Operational Warehouse Question",
            value=st.session_state[state_q_key],
            key=f"{key_prefix}_input_box",
            placeholder="e.g., Why was SKU-000209 rejected from all locations?",
        )
        run_btn = st.button(
            "Analyze with LangGraph Assistant",
            type="primary",
            key=f"{key_prefix}_btn_submit",
        )

    if run_btn:
        if len(question.strip()) < 3:
            st.warning("Please enter a question of at least 3 characters.")
        else:
            st.session_state[state_q_key] = question.strip()
            with st.spinner("Running 6-node LangGraph SQL retrieval & grounding verification..."):
                try:
                    resp = client.query_assistant(
                        question=question.strip(),
                        warehouse_id=warehouse_id,
                    )
                    st.session_state[state_res_key] = resp
                except FrontendAPIError as err:
                    render_api_error(err, title="Decision Assistant Query Failed")

    last_resp = st.session_state.get(state_res_key)
    if last_resp:
        ground_badge = status_pill_html("HEALTHY" if last_resp.get("grounding_verified") else "HIGH")
        st.markdown(
            f"**Intent:** `{last_resp.get('classified_intent')}` &nbsp;|&nbsp; "
            f"**Mode:** `{last_resp.get('execution_mode')}` &nbsp;|&nbsp; "
            f"**SQL Grounding Verified:** {ground_badge}",
            unsafe_allow_html=True,
        )
        st.info(last_resp.get("answer", ""))

        findings = last_resp.get("key_findings") or []
        if findings:
            st.markdown("**Key Grounded Findings:**")
            for item in findings:
                st.markdown(f"- {item}")

        actions = last_resp.get("recommended_actions") or []
        if actions:
            st.markdown("**Recommended Operational Actions:**")
            for act in actions:
                st.markdown(f"- {act}")

        cited = last_resp.get("cited_entity_ids") or []
        if cited:
            st.caption(f"Cited Database Entity IDs: `{', '.join(cited)}`")

        source_refs = last_resp.get("source_references") or []
        if source_refs:
            with st.expander(f"Inspect {len(source_refs)} Verified Source Record References"):
                st.dataframe(pd.DataFrame(source_refs), width="stretch", hide_index=True)

        trace = last_resp.get("trace") or []
        if trace:
            with st.expander("Inspect 6-Node LangGraph Execution Trace"):
                st.dataframe(pd.DataFrame(trace), width="stretch", hide_index=True)
