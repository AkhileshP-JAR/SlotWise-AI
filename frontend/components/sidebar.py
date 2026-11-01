"""Shared sidebar controller providing live API health, global warehouse filtering, and operator config."""

from __future__ import annotations

import os
from typing import Any

import streamlit as st

from frontend.components.api_client import (
    APIUnavailableError,
    FrontendAPIError,
    SlotWiseAPIClient,
    get_default_backend_url,
)
from frontend.components.theme import apply_enterprise_theme, status_pill_html


def init_page_and_sidebar(
    page_title: str,
    page_icon: str = "🏭",
) -> tuple[SlotWiseAPIClient, str | None, list[dict[str, Any]], APIUnavailableError | None]:
    """Initialize Streamlit page config, enterprise CSS, global sidebar filters, and live API probe.

    Returns:
        (api_client, selected_warehouse_id, warehouses_list, api_unavailable_error)
    """
    st.set_page_config(
        page_title=f"{page_title} | SlotWise AI",
        page_icon=page_icon,
        layout="wide",
        initial_sidebar_state="expanded",
    )
    apply_enterprise_theme()

    if "backend_url" not in st.session_state:
        st.session_state["backend_url"] = get_default_backend_url()
    if "write_token" not in st.session_state:
        st.session_state["write_token"] = os.getenv("SLOTWISE_API_WRITE_TOKEN", "")
    if "actor_id" not in st.session_state:
        st.session_state["actor_id"] = "STREAMLIT_OPERATOR"
    if "global_warehouse_id" not in st.session_state:
        st.session_state["global_warehouse_id"] = "ALL"

    custom_transport = st.session_state.get("_test_httpx_transport")

    with st.sidebar:
        st.markdown("### 🏭 SlotWise AI")
        st.caption("Intelligent Warehouse Slotting & Decision Intelligence (`v1.0.0`)")

        with st.expander("⚙️ API & Operator Settings", expanded=False):
            new_url = st.text_input(
                "FastAPI Backend URL",
                value=st.session_state["backend_url"],
                key="input_backend_url",
            )
            new_actor = st.text_input(
                "Operator ID (X-Actor)",
                value=st.session_state["actor_id"],
                key="input_actor_id",
            )
            new_token = st.text_input(
                "Write Auth Token (Optional)",
                value=st.session_state["write_token"],
                type="password",
                key="input_write_token",
                help="Required on mutating actions only when SLOTWISE_API_WRITE_TOKEN is configured on the server.",
            )
            st.session_state["backend_url"] = new_url.strip() or get_default_backend_url()
            st.session_state["actor_id"] = new_actor.strip() or "STREAMLIT_OPERATOR"
            st.session_state["write_token"] = new_token

        client = SlotWiseAPIClient(
            base_url=st.session_state["backend_url"],
            write_token=st.session_state["write_token"],
            actor=st.session_state["actor_id"],
            transport=custom_transport,
        )

        # Probe live FastAPI health & fetch warehouse list for global scope selector
        offline_err: APIUnavailableError | None = None
        warehouses: list[dict[str, Any]] = []
        health_info: dict[str, Any] | None = None

        try:
            health_info = client.get_health()
            wh_resp = client.list_warehouses(page=1, page_size=100)
            warehouses = wh_resp.get("items", [])
        except APIUnavailableError as exc:
            offline_err = exc
        except FrontendAPIError:
            # Health endpoint reachable or returned degraded HTTP error
            pass

        if offline_err is not None:
            st.markdown(
                f"**Backend Status:** {status_pill_html('OFFLINE')}",
                unsafe_allow_html=True,
            )
            st.caption(f"Target: `{client.base_url}`")
        else:
            api_stat = (health_info or {}).get("status", "ONLINE")
            db_ok = (health_info or {}).get("database_connected", True)
            st.markdown(
                f"**Backend Status:** {status_pill_html(api_stat)} &nbsp; DB: {'✅' if db_ok else '⚠️'}",
                unsafe_allow_html=True,
            )

        st.divider()
        st.markdown("#### 🌐 Global Facility Filter")

        wh_options: list[str] = ["ALL"] + [w["warehouse_id"] for w in warehouses]
        wh_labels: dict[str, str] = {"ALL": "All Warehouses (Enterprise)"}
        for w in warehouses:
            wh_labels[w["warehouse_id"]] = f"{w['warehouse_id']} — {w['warehouse_name']} ({w['city']})"

        curr_wh = st.session_state.get("global_warehouse_id", "ALL")
        if curr_wh not in wh_options:
            curr_wh = "ALL"
            st.session_state["global_warehouse_id"] = "ALL"

        chosen_wh = st.selectbox(
            "Warehouse Scope",
            options=wh_options,
            index=wh_options.index(curr_wh),
            format_func=lambda wid: wh_labels.get(wid, wid),
            key="sidebar_warehouse_select",
        )
        st.session_state["global_warehouse_id"] = chosen_wh

        if st.button("🔄 Refresh Live Telemetry", width="stretch", key="btn_sidebar_refresh"):
            st.cache_data.clear()
            st.rerun()

    selected_warehouse_id = (
        None if st.session_state["global_warehouse_id"] == "ALL" else st.session_state["global_warehouse_id"]
    )
    return client, selected_warehouse_id, warehouses, offline_err


def format_scope_badge(warehouse_id: str | None) -> str:
    """Format the top-right page header facility scope chip."""
    return f"Facility Scope: {warehouse_id}" if warehouse_id else "Facility Scope: ALL WAREHOUSES"
