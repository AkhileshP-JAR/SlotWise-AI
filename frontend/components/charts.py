"""Enterprise Plotly visualization builders for SlotWise AI."""

from __future__ import annotations

from typing import Any

import plotly.graph_objects as go

ZONE_COLORS: dict[str, str] = {
    "FAST_PICK": "#1D4ED8",
    "GENERAL": "#059669",
    "BULK_RESERVE": "#64748B",
    "COLD_CHAIN": "#0284C7",
    "HAZMAT": "#DC2626",
    "OVERSIZE": "#D97706",
}

SEVERITY_COLORS: dict[str, str] = {
    "CRITICAL": "#DC2626",
    "HIGH": "#EA580C",
    "MEDIUM": "#D97706",
    "LOW": "#2563EB",
}


def _apply_base_layout(fig: go.Figure, title: str, height: int = 380) -> go.Figure:
    fig.update_layout(
        title={"text": title, "font": {"size": 15, "color": "#0F172A"}},
        height=height,
        margin={"l": 45, "r": 25, "t": 48, "b": 42},
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#F8FAFC",
        font={"family": "Inter, Segoe UI, sans-serif", "color": "#1E293B", "size": 12},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1.0},
    )
    fig.update_xaxes(showgrid=True, gridcolor="#E2E8F0", zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor="#E2E8F0", zeroline=False)
    return fig


def build_velocity_and_abc_figures(sku_kpis: dict[str, Any]) -> tuple[go.Figure, go.Figure]:
    """Create SKU Demand Velocity tier bar chart and ABC/XYZ distribution chart."""
    vel_labels = ["FAST", "MEDIUM", "SLOW"]
    vel_vals = [
        int(sku_kpis.get("fast_movers", 0)),
        int(sku_kpis.get("medium_movers", 0)),
        int(sku_kpis.get("slow_movers", 0)),
    ]
    fig_vel = go.Figure(
        data=[
            go.Bar(
                x=vel_labels,
                y=vel_vals,
                marker_color=["#1D4ED8", "#059669", "#64748B"],
                text=vel_vals,
                textposition="auto",
            )
        ]
    )
    _apply_base_layout(fig_vel, "SKU Demand Velocity Classification", height=320)
    fig_vel.update_yaxes(title_text="SKU Count")

    abc = sku_kpis.get("abc_distribution", {})
    xyz = sku_kpis.get("xyz_distribution", {})
    fig_abc = go.Figure(
        data=[
            go.Bar(
                name="ABC Value Tier (A/B/C)",
                x=["Class A / X", "Class B / Y", "Class C / Z"],
                y=[int(abc.get("A", 0)), int(abc.get("B", 0)), int(abc.get("C", 0))],
                marker_color="#1D4ED8",
                text=[int(abc.get("A", 0)), int(abc.get("B", 0)), int(abc.get("C", 0))],
                textposition="auto",
            ),
            go.Bar(
                name="XYZ Variability (X/Y/Z)",
                x=["Class A / X", "Class B / Y", "Class C / Z"],
                y=[int(xyz.get("X", 0)), int(xyz.get("Y", 0)), int(xyz.get("Z", 0))],
                marker_color="#059669",
                text=[int(xyz.get("X", 0)), int(xyz.get("Y", 0)), int(xyz.get("Z", 0))],
                textposition="auto",
            ),
        ]
    )
    fig_abc.update_layout(barmode="group")
    _apply_base_layout(fig_abc, "ABC Revenue Value vs. XYZ Demand Stability", height=320)
    fig_abc.update_yaxes(title_text="SKU Count")
    return fig_vel, fig_abc


def build_warehouse_floorplan_figure(
    warehouse_detail: dict[str, Any],
    locations: list[dict[str, Any]],
    color_mode: str = "zone_type",
    selected_location_id: str | None = None,
) -> go.Figure:
    """Build an interactive 2D physical warehouse floorplan showing depot, aisles, blocked corridors, and slots."""
    fig = go.Figure()

    # 1. Draw aisle corridors (solid slate for active, dashed crimson for blocked)
    aisles = warehouse_detail.get("aisles", [])
    shown_active_aisle = False
    shown_blocked_aisle = False
    for a in aisles:
        is_blk = bool(a.get("is_blocked"))
        fig.add_trace(
            go.Scatter(
                x=[a["x_start"], a["x_end"]],
                y=[a["y_start"], a["y_end"]],
                mode="lines",
                line={
                    "color": "#DC2626" if is_blk else "#CBD5E1",
                    "width": 5 if is_blk else 2,
                    "dash": "dash" if is_blk else "solid",
                },
                name="Blocked Aisle Corridor" if is_blk else "Active Aisle Corridor",
                showlegend=(is_blk and not shown_blocked_aisle) or (not is_blk and not shown_active_aisle),
                hoverinfo="text",
                text=f"Aisle {a['aisle_id']} (Zone {a['zone_id']}) | Blocked={is_blk} | Congestion={a['congestion_factor']}x",
            )
        )
        if is_blk:
            shown_blocked_aisle = True
        else:
            shown_active_aisle = True

    # 2. Plot storage locations grouped by Zone Type or colored continuously by Volume Utilization %
    if locations:
        if color_mode == "utilization":
            xs = [float(loc["x_coordinate"]) for loc in locations]
            ys = [float(loc["y_coordinate"]) for loc in locations]
            utils = [
                max(float(loc["volume_utilization_pct"]), float(loc["weight_utilization_pct"]))
                for loc in locations
            ]
            hovers = [
                (
                    f"<b>{loc['location_id']}</b> ({loc.get('zone_type', 'N/A')})<br>"
                    f"Zone: {loc['zone_id']} | Temp: {loc.get('temperature_type', 'N/A')}<br>"
                    f"Access: {loc['accessibility_type']} | Policy: {loc['storage_policy']}<br>"
                    f"Weight: {loc['current_weight_kg']:.1f}/{loc['max_weight_kg']:.1f} kg ({loc['weight_utilization_pct']}%)<br>"
                    f"Volume: {loc['current_volume_m3']:.3f}/{loc['max_volume_m3']:.3f} m³ ({loc['volume_utilization_pct']}%)<br>"
                    f"Active SKUs: {', '.join(loc.get('active_sku_ids') or []) or 'Empty'}"
                )
                for loc in locations
            ]
            fig.add_trace(
                go.Scatter(
                    x=xs,
                    y=ys,
                    mode="markers",
                    name="Storage Slots (Peak Util %)",
                    marker={
                        "size": 10,
                        "color": utils,
                        "colorscale": "YlOrRd",
                        "cmin": 0,
                        "cmax": 100,
                        "showscale": True,
                        "colorbar": {"title": "Util %", "thickness": 14},
                        "line": {"width": 0.8, "color": "#334155"},
                    },
                    hovertemplate="%{text}<extra></extra>",
                    text=hovers,
                )
            )
        else:
            by_zone_type: dict[str, list[dict[str, Any]]] = {}
            for loc in locations:
                zt = str(loc.get("zone_type") or "GENERAL")
                by_zone_type.setdefault(zt, []).append(loc)

            for zt, z_locs in sorted(by_zone_type.items()):
                fig.add_trace(
                    go.Scatter(
                        x=[float(loc["x_coordinate"]) for loc in z_locs],
                        y=[float(loc["y_coordinate"]) for loc in z_locs],
                        mode="markers",
                        name=zt,
                        marker={
                            "size": 10,
                            "symbol": [
                                "x"
                                if (loc.get("is_blocked") or loc.get("aisle_is_blocked"))
                                else ("circle" if loc.get("active_sku_ids") else "circle-open")
                                for loc in z_locs
                            ],
                            "color": ZONE_COLORS.get(zt, "#475569"),
                            "line": {"width": 1.2, "color": ZONE_COLORS.get(zt, "#475569")},
                        },
                        hovertemplate="%{text}<extra></extra>",
                        text=[
                            (
                                f"<b>{loc['location_id']}</b> [{zt}]<br>"
                                f"Zone: {loc['zone_id']} | Temp: {loc.get('temperature_type', 'N/A')}<br>"
                                f"Weight Util: {loc['weight_utilization_pct']}% | Vol Util: {loc['volume_utilization_pct']}%<br>"
                                f"Blocked: {bool(loc.get('is_blocked') or loc.get('aisle_is_blocked'))}<br>"
                                f"Active SKUs: {', '.join(loc.get('active_sku_ids') or []) or 'Empty'}"
                            )
                            for loc in z_locs
                        ],
                    )
                )

        # Highlight selected slot if specified
        if selected_location_id:
            sel = next((loc for loc in locations if loc["location_id"] == selected_location_id), None)
            if sel:
                fig.add_trace(
                    go.Scatter(
                        x=[float(sel["x_coordinate"])],
                        y=[float(sel["y_coordinate"])],
                        mode="markers+text",
                        name=f"Selected: {selected_location_id}",
                        text=[selected_location_id],
                        textposition="top center",
                        marker={
                            "size": 17,
                            "symbol": "star",
                            "color": "#F59E0B",
                            "line": {"width": 2, "color": "#0F172A"},
                        },
                    )
                )

    # 3. Staging Depot marker
    fig.add_trace(
        go.Scatter(
            x=[float(warehouse_detail.get("depot_x", 0.0))],
            y=[float(warehouse_detail.get("depot_y", 0.0))],
            mode="markers+text",
            name="Staging / Dispatch Dock",
            text=["DOCK"],
            textposition="bottom right",
            marker={"size": 15, "symbol": "diamond", "color": "#0F172A", "line": {"width": 2, "color": "#F8FAFC"}},
        )
    )

    _apply_base_layout(
        fig,
        f"2D Facility Layout & Slot Map — {warehouse_detail['warehouse_id']} ({warehouse_detail['warehouse_name']})",
        height=470,
    )
    fig.update_xaxes(title_text="Warehouse X Coordinate (meters)")
    fig.update_yaxes(title_text="Warehouse Y Coordinate (meters)")
    return fig


def build_zone_utilization_figure(zones: list[dict[str, Any]]) -> go.Figure:
    """Create grouped bar chart of Slot Occupancy %, Weight Util %, and Volume Util % across zones."""
    z_labels = [f"{z['zone_id']} ({z['zone_type']})" for z in zones]
    slot_u = [
        round((int(z.get("occupied_location_count", 0)) / max(int(z.get("location_count", 1)), 1)) * 100.0, 2)
        for z in zones
    ]
    wt_u = [
        round((float(z.get("occupied_weight_kg", 0.0)) / max(float(z.get("max_weight_kg", 1.0)), 1.0)) * 100.0, 2)
        for z in zones
    ]
    vol_u = [
        round((float(z.get("occupied_volume_m3", 0.0)) / max(float(z.get("max_volume_m3", 1.0)), 1e-6)) * 100.0, 2)
        for z in zones
    ]

    fig = go.Figure(
        data=[
            go.Bar(name="Slot Occupancy %", x=z_labels, y=slot_u, marker_color="#1D4ED8"),
            go.Bar(name="Volume Util %", x=z_labels, y=vol_u, marker_color="#059669"),
            go.Bar(name="Weight Util %", x=z_labels, y=wt_u, marker_color="#D97706"),
        ]
    )
    fig.update_layout(barmode="group")
    _apply_base_layout(fig, "Zone Capacity & Slot Occupancy Breakdown (%)", height=350)
    fig.update_yaxes(
        title_text="Utilization (%)", range=[0, max(105.0, max(slot_u + vol_u + wt_u + [10.0]) * 1.1)]
    )
    return fig


def build_sku_demand_trend_figure(
    sku_id: str,
    demand_trend: list[dict[str, Any]],
    avg_daily_demand: float | None = None,
) -> go.Figure:
    """Plot historical daily units ordered and order-line count for a SKU."""
    dates = [pt["order_date"] for pt in demand_trend]
    units = [int(pt["units_ordered"]) for pt in demand_trend]
    lines = [int(pt["order_lines"]) for pt in demand_trend]

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=dates,
            y=units,
            name="Units Ordered",
            marker_color="#2563EB",
            opacity=0.82,
        )
    )
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=lines,
            mode="lines+markers",
            name="Order Lines",
            line={"color": "#059669", "width": 2},
            marker={"size": 5},
        )
    )
    if avg_daily_demand is not None and dates:
        fig.add_hline(
            y=avg_daily_demand,
            line_dash="dash",
            line_color="#D97706",
            annotation_text=f"ADV = {avg_daily_demand:.2f} units/day",
            annotation_position="top right",
        )
    _apply_base_layout(fig, f"Historical Daily Order Demand — {sku_id}", height=330)
    fig.update_xaxes(title_text="Order Date")
    fig.update_yaxes(title_text="Quantity / Order Lines")
    return fig


def build_candidate_score_comparison_figure(candidates: list[dict[str, Any]]) -> go.Figure:
    """Plot weighted/normalized 6-component score profiles across top feasible candidate locations."""
    labels = [
        f"#{c.get('rank_position', i + 1)} {c['location_id']} ({c['zone_type']})" for i, c in enumerate(candidates)
    ]
    components = [
        ("velocity_score", "Velocity (0.30)", "#1D4ED8"),
        ("pick_frequency_score", "Pick Freq (0.20)", "#2563EB"),
        ("distance_score", "Dock Proximity (0.15)", "#059669"),
        ("capacity_score", "Capacity Fit (0.15)", "#10B981"),
        ("replenishment_score", "Replenishment (0.10)", "#D97706"),
        ("compatibility_score", "Zone Affinity (0.10)", "#6366F1"),
    ]
    weights = {
        "velocity_score": 0.30,
        "pick_frequency_score": 0.20,
        "distance_score": 0.15,
        "capacity_score": 0.15,
        "replenishment_score": 0.10,
        "compatibility_score": 0.10,
    }

    fig = go.Figure()
    for field_key, display_name, color in components:
        w = weights[field_key]
        weighted_vals = [round(float(c["score_breakdown"].get(field_key, 0.0)) * w, 4) for c in candidates]
        raw_vals = [round(float(c["score_breakdown"].get(field_key, 0.0)), 4) for c in candidates]
        fig.add_trace(
            go.Bar(
                name=display_name,
                x=labels,
                y=weighted_vals,
                marker_color=color,
                customdata=raw_vals,
                hovertemplate=f"<b>%{{x}}</b><br>{display_name}<br>Weighted Contribution: %{{y:.4f}}<br>Raw Component Score: %{{customdata:.4f}}<extra></extra>",
            )
        )
    fig.update_layout(barmode="stack")
    _apply_base_layout(fig, "Explainable 6-Factor Weighted Candidate Score Decomposition", height=370)
    fig.update_yaxes(title_text="Total Weighted Score [0.0 – 1.0]", range=[0, 1.05])
    return fig


def build_rejection_reasons_figure(rejection_counts: dict[str, int]) -> go.Figure:
    """Horizontal bar chart of hard-constraint rejection counts for a SKU."""
    sorted_items = sorted(rejection_counts.items(), key=lambda kv: kv[1])
    reasons = [k for k, _ in sorted_items]
    counts = [v for _, v in sorted_items]

    fig = go.Figure(
        data=[
            go.Bar(
                x=counts,
                y=reasons,
                orientation="h",
                marker_color="#DC2626",
                text=counts,
                textposition="auto",
            )
        ]
    )
    _apply_base_layout(fig, "Hard-Constraint Candidate Rejection Breakdown", height=330)
    fig.update_xaxes(title_text="Rejected Locations Count")
    return fig


def build_pick_route_comparison_figure(sample_order: dict[str, Any]) -> go.Figure:
    """Overlay Baseline pick tour vs. Optimized (Slotting + Nearest-Neighbor + 2-Opt) pick tour on 2D coordinates."""
    base_wp = sample_order.get("baseline_waypoints", [])
    opt_wp = sample_order.get("optimized_waypoints", [])
    base_seq = sample_order.get("baseline_sequence", [])
    opt_seq = sample_order.get("optimized_sequence", [])

    fig = go.Figure()

    if base_wp:
        bx = [pt[0] for pt in base_wp]
        by = [pt[1] for pt in base_wp]
        b_labels = ["DOCK"] + [f"B{i + 1}:{loc}" for i, loc in enumerate(base_seq)] + ["DOCK"]
        fig.add_trace(
            go.Scatter(
                x=bx,
                y=by,
                mode="lines+markers",
                name=f"Baseline Tour ({sample_order['baseline_distance_m']:.1f} m)",
                line={"color": "#DC2626", "width": 2.5, "dash": "dot"},
                marker={"size": 8, "color": "#DC2626"},
                text=b_labels[: len(bx)],
                hovertemplate="<b>%{text}</b><br>X=%{x:.1f}m, Y=%{y:.1f}m<extra>Baseline</extra>",
            )
        )

    if opt_wp:
        ox = [pt[0] for pt in opt_wp]
        oy = [pt[1] for pt in opt_wp]
        o_labels = ["DOCK"] + [f"O{i + 1}:{loc}" for i, loc in enumerate(opt_seq)] + ["DOCK"]
        fig.add_trace(
            go.Scatter(
                x=ox,
                y=oy,
                mode="lines+markers+text",
                name=f"Optimized Tour ({sample_order['optimized_distance_m']:.1f} m)",
                line={"color": "#059669", "width": 3},
                marker={"size": 10, "color": "#059669"},
                text=["" if i in (0, len(ox) - 1) else f"#{i}" for i in range(len(ox))],
                textposition="top center",
                customdata=o_labels[: len(ox)],
                hovertemplate="<b>%{customdata}</b><br>X=%{x:.1f}m, Y=%{y:.1f}m<extra>Optimized</extra>",
            )
        )

        # Highlight Dock origin
        fig.add_trace(
            go.Scatter(
                x=[ox[0]],
                y=[oy[0]],
                mode="markers+text",
                name="Dispatch Dock",
                text=["DOCK"],
                textposition="bottom right",
                marker={"size": 15, "symbol": "diamond", "color": "#0F172A"},
            )
        )

    _apply_base_layout(
        fig,
        f"2D Pick Tour Comparison — Order {sample_order['order_id']} ({sample_order['warehouse_id']}, Saved {sample_order['distance_saved_m']:.1f} m)",
        height=430,
    )
    fig.update_xaxes(title_text="Warehouse X (meters)")
    fig.update_yaxes(title_text="Warehouse Y (meters)")
    return fig


def build_picking_attribution_figure(sim_result: dict[str, Any]) -> go.Figure:
    """Bar chart separating Baseline, Pure Slotting Gain, Pure Routing Gain, and Combined Optimized Distance."""
    stages = [
        "1. Baseline Layout +\nEntry Sequence",
        "2. Baseline Layout +\n2-Opt Route Only",
        "3. Optimized Slotting +\nEntry Sequence",
        "4. Optimized Slotting +\n2-Opt Route (Combined)",
    ]
    vals = [
        float(sim_result.get("baseline_distance_m", 0.0)),
        float(sim_result.get("baseline_slotting_optimized_route_distance_m", 0.0)),
        float(sim_result.get("optimized_slotting_baseline_route_distance_m", 0.0)),
        float(sim_result.get("optimized_distance_m", 0.0)),
    ]
    fig = go.Figure(
        data=[
            go.Bar(
                x=stages,
                y=vals,
                marker_color=["#DC2626", "#D97706", "#2563EB", "#059669"],
                text=[f"{v:,.1f} m" for v in vals],
                textposition="auto",
            )
        ]
    )
    _apply_base_layout(fig, "Travel Distance Attribution: Slotting Relocation vs. Route Sequencing", height=350)
    fig.update_yaxes(title_text="Total Travel Distance (meters)")
    return fig


def build_replenishment_labor_figure(warehouse_summaries: list[dict[str, Any]]) -> go.Figure:
    """Compare required replenishment labor minutes against available shift capacity per warehouse."""
    wh_ids = [w["warehouse_id"] for w in warehouse_summaries]
    req_mins = [float(w["labor_minutes_required"]) for w in warehouse_summaries]
    cap_mins = [float(w["available_labor_minutes"]) for w in warehouse_summaries]
    colors = ["#DC2626" if w.get("is_overloaded") else "#059669" for w in warehouse_summaries]

    fig = go.Figure(
        data=[
            go.Bar(
                name="Required Labor (min)",
                x=wh_ids,
                y=req_mins,
                marker_color=colors,
                text=[
                    f"{m:,.0f}m ({w['labor_utilization_pct']}%)"
                    for m, w in zip(req_mins, warehouse_summaries, strict=False)
                ],
                textposition="auto",
            ),
            go.Bar(
                name="Available Daily Shift Capacity (min)",
                x=wh_ids,
                y=cap_mins,
                marker_color="#64748B",
                text=[f"{c:,.0f}m" for c in cap_mins],
                textposition="auto",
            ),
        ]
    )
    fig.update_layout(barmode="group")
    _apply_base_layout(fig, "Warehouse Replenishment Labor Demand vs. Shift Capacity", height=350)
    fig.update_yaxes(title_text="Labor Minutes")
    return fig


def build_exceptions_summary_figures(exceptions: list[dict[str, Any]]) -> tuple[go.Figure, go.Figure]:
    """Create stacked bar chart of Exception Issue Types by Severity and Resolution Status donut."""
    issue_types = sorted({e["issue_type"] for e in exceptions})
    severities = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]

    fig_issues = go.Figure()
    for sev in severities:
        counts = [
            sum(1 for e in exceptions if e["issue_type"] == it and e["severity"] == sev) for it in issue_types
        ]
        if any(counts):
            fig_issues.add_trace(
                go.Bar(
                    name=sev,
                    y=issue_types,
                    x=counts,
                    orientation="h",
                    marker_color=SEVERITY_COLORS.get(sev, "#64748B"),
                )
            )
    fig_issues.update_layout(barmode="stack")
    _apply_base_layout(fig_issues, "Detected Exceptions by Issue Type & Severity", height=350)
    fig_issues.update_xaxes(title_text="Exception Count")

    status_counts: dict[str, int] = {}
    for e in exceptions:
        st_val = e["resolution_status"]
        status_counts[st_val] = status_counts.get(st_val, 0) + 1

    fig_status = go.Figure(
        data=[
            go.Pie(
                labels=list(status_counts.keys()),
                values=list(status_counts.values()),
                hole=0.52,
                marker={
                    "colors": [
                        "#DC2626"
                        if k == "OPEN"
                        else ("#D97706" if k == "IN_REVIEW" else ("#059669" if k == "RESOLVED" else "#64748B"))
                        for k in status_counts
                    ]
                },
            )
        ]
    )
    _apply_base_layout(fig_status, "Exception Lifecycle Resolution Status", height=350)
    return fig_issues, fig_status


def build_scenario_comparison_figures(scenario: dict[str, Any]) -> tuple[go.Figure, go.Figure]:
    """Create side-by-side Baseline vs. Optimized KPI comparisons for a persisted Scenario."""
    base = scenario.get("baseline_metrics_json", {})
    opt = scenario.get("optimized_metrics_json", {})

    fig_travel = go.Figure(
        data=[
            go.Bar(
                name="Baseline Layout",
                x=["Pick Distance (m)", "Pick Time (sec)"],
                y=[float(base.get("pick_distance_m", 0.0)), float(base.get("pick_time_seconds", 0.0))],
                marker_color="#DC2626",
                text=[
                    f"{float(base.get('pick_distance_m', 0.0)):,.1f}",
                    f"{float(base.get('pick_time_seconds', 0.0)):,.1f}",
                ],
                textposition="auto",
            ),
            go.Bar(
                name="Optimized Layout",
                x=["Pick Distance (m)", "Pick Time (sec)"],
                y=[float(opt.get("pick_distance_m", 0.0)), float(opt.get("pick_time_seconds", 0.0))],
                marker_color="#059669",
                text=[
                    f"{float(opt.get('pick_distance_m', 0.0)):,.1f}",
                    f"{float(opt.get('pick_time_seconds', 0.0)):,.1f}",
                ],
                textposition="auto",
            ),
        ]
    )
    fig_travel.update_layout(barmode="group")
    _apply_base_layout(fig_travel, "Fulfillment Travel Distance & Time Comparison (Identical Orders)", height=340)

    fig_quality = go.Figure(
        data=[
            go.Bar(
                name="Baseline Layout",
                x=["Avg Assignment Score (×100)", "Slot Occupancy (%)", "Volume Util (%)", "Capacity Violations"],
                y=[
                    round(float(base.get("average_assignment_score", 0.0)) * 100.0, 2),
                    float(base.get("location_occupancy_pct", 0.0)),
                    float(base.get("volume_utilization_pct", 0.0)),
                    float(base.get("capacity_violation_locations", 0)),
                ],
                marker_color="#64748B",
            ),
            go.Bar(
                name="Optimized Layout",
                x=["Avg Assignment Score (×100)", "Slot Occupancy (%)", "Volume Util (%)", "Capacity Violations"],
                y=[
                    round(float(opt.get("average_assignment_score", 0.0)) * 100.0, 2),
                    float(opt.get("location_occupancy_pct", 0.0)),
                    float(opt.get("volume_utilization_pct", 0.0)),
                    float(opt.get("capacity_violation_locations", 0)),
                ],
                marker_color="#1D4ED8",
            ),
        ]
    )
    fig_quality.update_layout(barmode="group")
    _apply_base_layout(fig_quality, "Slotting Quality, Utilization & Constraint Violations", height=340)
    return fig_travel, fig_quality
