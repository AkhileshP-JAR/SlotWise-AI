"""Page 02 — Interactive 2D Warehouse Topology, Zone Utilization & Deep Slot Inspector."""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pandas as pd
import streamlit as st

from frontend.components.api_client import FrontendAPIError
from frontend.components.charts import (
    build_warehouse_floorplan_figure,
    build_zone_utilization_figure,
)
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
    client, global_wh_id, warehouses, offline_err = init_page_and_sidebar(
        page_title="02 Warehouse Explorer",
        page_icon="🗺️",
    )

    render_page_header(
        title="02 · Warehouse Topology & 2D Floorplan Explorer",
        subtitle="Inspect physical zones, permitted hazard classes, blocked aisle corridors, 2D slot coordinates, and historical bin occupancy.",
        scope_label=format_scope_badge(global_wh_id),
    )

    if offline_err is not None:
        render_api_unavailable_banner(offline_err, backend_url=client.base_url)
        return

    if not warehouses:
        render_empty_dataset_banner()
        return

    wh_ids = [w["warehouse_id"] for w in warehouses]
    default_idx = wh_ids.index(global_wh_id) if global_wh_id in wh_ids else 0

    ctrl1, ctrl2, ctrl3, ctrl4, ctrl5 = st.columns([1.4, 1.1, 1.1, 1.1, 1.1])
    with ctrl1:
        active_wh_id = st.selectbox(
            "Target Facility",
            options=wh_ids,
            index=default_idx,
            format_func=lambda wid: next(
                (f"{w['warehouse_id']} — {w['warehouse_name']}" for w in warehouses if w["warehouse_id"] == wid),
                wid,
            ),
            key="wh_exp_facility",
        )
    with ctrl2:
        zone_type_filter = st.selectbox(
            "Zone Type",
            options=["ALL", "FAST_PICK", "GENERAL", "BULK_RESERVE", "COLD_CHAIN", "HAZMAT", "OVERSIZE"],
            key="wh_exp_zone_type",
        )
    with ctrl3:
        temp_filter = st.selectbox(
            "Temperature",
            options=["ALL", "AMBIENT", "CONTROLLED_ROOM", "CHILLED", "FROZEN"],
            key="wh_exp_temp",
        )
    with ctrl4:
        occ_filter = st.selectbox(
            "Occupancy Filter",
            options=["ALL", "OCCUPIED_ONLY", "EMPTY_ONLY", "BLOCKED_ONLY"],
            key="wh_exp_occ",
        )
    with ctrl5:
        color_mode = st.selectbox(
            "Map Color Encoding",
            options=["zone_type", "utilization"],
            format_func=lambda m: "Zone Type & Blockage" if m == "zone_type" else "Peak Capacity Util (%)",
            key="wh_exp_color_mode",
        )

    is_blocked_param: bool | None = True if occ_filter == "BLOCKED_ONLY" else None
    occupied_param: bool | None = (
        True if occ_filter == "OCCUPIED_ONLY" else (False if occ_filter == "EMPTY_ONLY" else None)
    )

    with st.spinner(f"Loading 2D topology and slots for {active_wh_id}..."):
        try:
            wh_detail = client.get_warehouse(active_wh_id)
            locs_resp = client.list_locations(
                warehouse_id=active_wh_id,
                zone_type=None if zone_type_filter == "ALL" else zone_type_filter,
                temperature_type=None if temp_filter == "ALL" else temp_filter,
                is_blocked=is_blocked_param,
                occupied_only=occupied_param,
                page=1,
                page_size=500,
            )
        except FrontendAPIError as err:
            render_api_error(err, title="Warehouse Explorer Query Failed")
            return

    locations = locs_resp.get("items", [])

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        render_kpi_card(
            "Facility Area & Dock",
            f"{wh_detail.get('total_area_sqft', 0):,.0f} sq.ft",
            f"Staging Dock: ({wh_detail.get('depot_x', 0.0)}m, {wh_detail.get('depot_y', 0.0)}m)",
            accent="blue",
        )
    with k2:
        render_kpi_card(
            "Zones & Aisles",
            f"{wh_detail.get('zone_count', 0)} Zones / {wh_detail.get('aisle_count', 0)} Aisles",
            f"{wh_detail.get('blocked_aisle_count', 0)} blocked aisle corridor(s)",
            accent="rose" if int(wh_detail.get("blocked_aisle_count", 0)) > 0 else "emerald",
        )
    with k3:
        render_kpi_card(
            "Facility Slot Occupancy",
            f"{wh_detail.get('occupied_locations', 0)} / {wh_detail.get('total_locations', 0)}",
            f"{wh_detail.get('location_utilization_pct', 0.0)}% occupied",
            accent="indigo",
        )
    with k4:
        render_kpi_card(
            "Matching Filter Slots",
            f"{locs_resp.get('meta', {}).get('total_items', len(locations))}",
            f"Vol Util: {wh_detail.get('volume_utilization_pct', 0.0)}% · Wt Util: {wh_detail.get('weight_utilization_pct', 0.0)}%",
            accent="emerald",
        )

    selected_loc_id: str | None = None
    if locations:
        selected_loc_id = st.selectbox(
            "Highlight & Inspect Storage Slot on 2D Floorplan",
            options=[loc["location_id"] for loc in locations],
            format_func=lambda lid: next(
                (
                    f"{loc['location_id']} | {loc['zone_type']} ({loc['temperature_type']}) | "
                    f"Vol {loc['volume_utilization_pct']}% | SKUs: {', '.join(loc['active_sku_ids']) or 'Empty'}"
                    for loc in locations
                    if loc["location_id"] == lid
                ),
                lid,
            ),
            key="wh_exp_selected_loc",
        )

    fig_map = build_warehouse_floorplan_figure(
        warehouse_detail=wh_detail,
        locations=locations,
        color_mode=color_mode,
        selected_location_id=selected_loc_id,
    )
    st.plotly_chart(fig_map, width="stretch")

    zones = wh_detail.get("zones", [])
    if zones:
        zc1, zc2 = st.columns([1.25, 1])
        with zc1:
            st.plotly_chart(build_zone_utilization_figure(zones), width="stretch")
        with zc2:
            render_section_header("🛡️ Zone Environmental & Hazard Allowances")
            z_df = pd.DataFrame(zones)[
                [
                    "zone_id",
                    "zone_type",
                    "temperature_type",
                    "permitted_hazards",
                    "location_count",
                    "occupied_location_count",
                ]
            ]
            st.dataframe(z_df, width="stretch", hide_index=True)

    render_section_header("🔎 Deep Storage Slot Inspector & Assignment Lineage")
    if not locations:
        st.info("No storage locations match the selected filter criteria.")
    elif selected_loc_id:
        try:
            loc_detail = client.get_location(selected_loc_id)
            d1, d2, d3 = st.columns(3)
            with d1:
                st.markdown(
                    f"**Slot ID:** `{loc_detail['location_id']}`  \n"
                    f"**Zone / Aisle:** `{loc_detail['zone_id']}` (`{loc_detail['zone_type']}`) / `{loc_detail['aisle_id']}`  \n"
                    f"**Coordinates:** `X={loc_detail['x_coordinate']}m, Y={loc_detail['y_coordinate']}m`"
                )
            with d2:
                st.markdown(
                    f"**Dimensions:** `{loc_detail['width_cm']} × {loc_detail['height_cm']} × {loc_detail['depth_cm']} cm`  \n"
                    f"**Weight Headroom:** `{loc_detail['available_weight_kg']:.2f} kg` free of `{loc_detail['max_weight_kg']:.1f} kg`  \n"
                    f"**Volume Headroom:** `{loc_detail['available_volume_m3']:.4f} m³` free of `{loc_detail['max_volume_m3']:.4f} m³`"
                )
            with d3:
                st.markdown(
                    f"**Accessibility:** `{loc_detail['accessibility_type']}`  \n"
                    f"**Storage Policy:** `{loc_detail['storage_policy']}`  \n"
                    f"**Permitted Hazards:** `{', '.join(loc_detail.get('allowed_hazard_classes', []))}`"
                )

            tab_act, tab_hist, tab_tbl = st.tabs(
                [
                    f"Active Assignments ({len(loc_detail.get('active_assignments', []))})",
                    f"Historical Lineage ({len(loc_detail.get('historical_assignments', []))})",
                    f"Filtered Slots Table ({len(locations)})",
                ]
            )
            with tab_act:
                act_list = loc_detail.get("active_assignments", [])
                if act_list:
                    st.dataframe(pd.DataFrame(act_list), width="stretch", hide_index=True)
                else:
                    st.info("This storage location currently has 0 ACTIVE SKU assignments.")
            with tab_hist:
                hist_list = loc_detail.get("historical_assignments", [])
                if hist_list:
                    st.dataframe(pd.DataFrame(hist_list), width="stretch", hide_index=True)
                else:
                    st.caption("No superseded, proposed, or rejected historical records for this slot.")
            with tab_tbl:
                st.dataframe(pd.DataFrame(locations), width="stretch", hide_index=True)
        except FrontendAPIError as err:
            render_api_error(err, title="Failed to Load Location Detail")


if __name__ == "__main__":
    main()
