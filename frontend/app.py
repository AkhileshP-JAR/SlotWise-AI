"""SlotWise AI — Enterprise Streamlit Frontend Entrypoint & Command Launchpad."""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import streamlit as st

from frontend.components.api_client import FrontendAPIError
from frontend.components.assistant_panel import render_decision_assistant_panel
from frontend.components.sidebar import format_scope_badge, init_page_and_sidebar
from frontend.components.theme import (
    render_api_error,
    render_api_unavailable_banner,
    render_empty_dataset_banner,
    render_kpi_card,
    render_page_header,
    render_section_header,
)


def main() -> None:
    client, warehouse_id, warehouses, offline_err = init_page_and_sidebar(
        page_title="Command Center",
        page_icon="🏭",
    )

    render_page_header(
        title="SlotWise AI — Intelligent Warehouse Slotting & Decision Platform",
        subtitle=(
            "End-to-end constrained slotting optimization (OR-Tools CP-SAT + Greedy/Swap), "
            "2D aisle-network pick routing, labor workload simulation, and audit-grade exception governance."
        ),
        scope_label=format_scope_badge(warehouse_id),
    )

    if offline_err is not None:
        render_api_unavailable_banner(offline_err, backend_url=client.base_url)
        return

    try:
        sys_status = client.get_system_status()
        overview = client.get_analytics_overview(warehouse_id=warehouse_id, sample_picking_orders=200)
    except FrontendAPIError as err:
        render_api_error(err, title="Failed to Load Platform Telemetry")
        return

    wh_kpis = overview.get("warehouse", {})
    sku_kpis = overview.get("sku", {})
    opt_kpis = overview.get("optimization", {})
    pick_kpis = overview.get("picking_simulated_estimates", {})
    exc_kpis = overview.get("exceptions", {})

    if int(wh_kpis.get("total_locations", 0)) == 0 or int(sku_kpis.get("total_skus", 0)) == 0:
        render_empty_dataset_banner()
    else:
        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            render_kpi_card(
                "Active Facilities & Slots",
                f"{wh_kpis.get('total_warehouses', 0)} WH / {wh_kpis.get('total_locations', 0):,}",
                f"{wh_kpis.get('location_utilization_pct', 0.0)}% slots occupied",
                accent="blue",
            )
        with c2:
            render_kpi_card(
                "Profiled SKUs",
                f"{sku_kpis.get('total_skus', 0):,}",
                f"{sku_kpis.get('fast_movers', 0)} Fast · {sku_kpis.get('medium_movers', 0)} Med · {sku_kpis.get('slow_movers', 0)} Slow",
                accent="indigo",
            )
        with c3:
            render_kpi_card(
                "Avg Slotting Score",
                f"{float(opt_kpis.get('average_assignment_score', 0.0)):.4f}",
                f"Latest Run: {opt_kpis.get('latest_run_id') or 'Baseline'}",
                accent="emerald",
            )
        with c4:
            render_kpi_card(
                "Simulated Pick Savings",
                f"{pick_kpis.get('distance_improvement_pct', 0.0)}%",
                f"{pick_kpis.get('distance_saved_m', 0.0):,.1f} m saved ({pick_kpis.get('orders_evaluated', 0)} orders)",
                accent="emerald",
            )
        with c5:
            render_kpi_card(
                "Open Exceptions",
                f"{exc_kpis.get('open_exceptions', 0)}",
                f"{exc_kpis.get('resolved_exceptions', 0)} resolved of {exc_kpis.get('total_exceptions', 0)} total",
                accent="rose" if int(exc_kpis.get("open_exceptions", 0)) > 0 else "emerald",
            )

    render_section_header("🧭 Enterprise Workspace Modules (9 Dedicated Pages)")
    nav_rows = [
        [
            (
                "pages/01_Overview.py",
                "📊 01 Overview",
                "Executive KPIs, ABC/XYZ matrices, F1 benchmark & telemetry.",
            ),
            (
                "pages/02_Warehouse_Explorer.py",
                "🗺️ 02 Warehouse Explorer",
                "2D physical floorplan, zones, blocked aisles & slot inspector.",
            ),
            (
                "pages/03_Inventory.py",
                "📦 03 Inventory & SKUs",
                "SKU search, demand profiles, 90-day trends & candidate slots.",
            ),
        ],
        [
            (
                "pages/04_Slotting_Optimizer.py",
                "⚙️ 04 Slotting Optimizer",
                "Run CP-SAT / Greedy optimization, compare candidates & approve/reject.",
            ),
            (
                "pages/05_Picking_Analytics.py",
                "🚶 05 Picking Analytics",
                "Observed pick telemetry & 2D baseline vs. 2-Opt route simulator.",
            ),
            (
                "pages/06_Replenishment.py",
                "🔄 06 Replenishment",
                "Warehouse shift labor utilization, overloads & what-if simulator.",
            ),
        ],
        [
            (
                "pages/07_Exceptions.py",
                "🚨 07 Exception Center",
                "Rule/operational exceptions, live scans & lifecycle state transitions.",
            ),
            (
                "pages/08_Scenario_Comparison.py",
                "⚖️ 08 Scenario Comparison",
                "Side-by-side Baseline vs. Optimized run scenario builder & deltas.",
            ),
            (
                "pages/09_System_Health.py",
                "🛡️ 09 System Health",
                "21-table DB counts, synthetic data generator/validator & audit log.",
            ),
        ],
    ]
    for row in nav_rows:
        cols = st.columns(3)
        for col, (page_path, label, desc) in zip(cols, row, strict=False):
            with col, st.container(border=True):
                st.page_link(page_path, label=label, width="stretch")
                st.caption(desc)

    st.caption(
        f"Active Dataset Profile: `{sys_status.get('active_profile')}` | "
        f"Dataset Version: `{sys_status.get('dataset_version') or 'N/A'}` | "
        f"Seed: `{sys_status.get('seed')}` | "
        f"Total Relational Records: `{sys_status.get('total_records', 0):,}`"
    )

    st.divider()
    render_decision_assistant_panel(client=client, warehouse_id=warehouse_id, key_prefix="home_asst")


if __name__ == "__main__":
    main()
