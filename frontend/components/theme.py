"""Enterprise visual design system, KPI cards, status badges, and resilient error/empty states."""

from __future__ import annotations

import html

import streamlit as st

from frontend.components.api_client import APIUnavailableError, FrontendAPIError

ACCENT_MAP: dict[str, str] = {
    "blue": "#1D4ED8",
    "emerald": "#059669",
    "amber": "#D97706",
    "rose": "#DC2626",
    "indigo": "#4F46E5",
    "slate": "#475569",
}

STATUS_COLORS: dict[str, tuple[str, str]] = {
    "HEALTHY": ("#DCFCE7", "#166534"),
    "ONLINE": ("#DCFCE7", "#166534"),
    "ACTIVE": ("#DCFCE7", "#166534"),
    "COMPLETED": ("#DCFCE7", "#166534"),
    "RESOLVED": ("#DCFCE7", "#166534"),
    "FAST": ("#DBEAFE", "#1E40AF"),
    "PROPOSED": ("#FEF3C7", "#92400E"),
    "PARTIAL": ("#FEF3C7", "#92400E"),
    "IN_REVIEW": ("#FEF3C7", "#92400E"),
    "PENDING": ("#FEF3C7", "#92400E"),
    "MEDIUM": ("#FEF3C7", "#92400E"),
    "HIGH": ("#FFEDD5", "#9A3412"),
    "CRITICAL": ("#FEE2E2", "#991B1B"),
    "OVERLOADED": ("#FEE2E2", "#991B1B"),
    "FAILED": ("#FEE2E2", "#991B1B"),
    "OPEN": ("#FEE2E2", "#991B1B"),
    "OFFLINE": ("#FEE2E2", "#991B1B"),
    "REJECTED": ("#F1F5F9", "#475569"),
    "SUPERSEDED": ("#F1F5F9", "#475569"),
    "DISMISSED": ("#F1F5F9", "#475569"),
    "SLOW": ("#F1F5F9", "#475569"),
    "LOW": ("#E0F2FE", "#075985"),
}


def apply_enterprise_theme() -> None:
    """Inject enterprise CSS typography, card elevation, and badge styles."""
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.4rem;
            padding-bottom: 2.5rem;
            max-width: 1440px;
        }
        .sw-header-card {
            background: linear-gradient(135deg, #0F172A 0%, #1E293B 100%);
            color: #F8FAFC;
            padding: 1.25rem 1.6rem;
            border-radius: 12px;
            margin-bottom: 1.25rem;
            border: 1px solid #334155;
            box-shadow: 0 4px 14px rgba(15, 23, 42, 0.08);
        }
        .sw-header-top {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1rem;
            flex-wrap: wrap;
        }
        .sw-header-title {
            font-size: 1.45rem;
            font-weight: 700;
            letter-spacing: -0.02em;
            margin: 0;
            color: #FFFFFF;
        }
        .sw-header-sub {
            font-size: 0.92rem;
            color: #CBD5E1;
            margin-top: 0.35rem;
            margin-bottom: 0;
        }
        .sw-scope-chip {
            background: rgba(59, 130, 246, 0.22);
            border: 1px solid rgba(96, 165, 250, 0.45);
            color: #DBEAFE;
            padding: 0.28rem 0.75rem;
            border-radius: 999px;
            font-size: 0.78rem;
            font-weight: 600;
            letter-spacing: 0.03em;
        }
        .sw-kpi-card {
            background: #FFFFFF;
            border: 1px solid #E2E8F0;
            border-radius: 10px;
            padding: 0.95rem 1.1rem;
            box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
            height: 100%;
            margin-bottom: 0.65rem;
        }
        .sw-kpi-label {
            font-size: 0.77rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            color: #64748B;
            margin-bottom: 0.3rem;
        }
        .sw-kpi-value {
            font-size: 1.55rem;
            font-weight: 700;
            color: #0F172A;
            line-height: 1.2;
        }
        .sw-kpi-delta {
            font-size: 0.80rem;
            font-weight: 500;
            color: #475569;
            margin-top: 0.32rem;
        }
        .sw-pill {
            display: inline-block;
            padding: 0.18rem 0.62rem;
            border-radius: 999px;
            font-size: 0.74rem;
            font-weight: 700;
            letter-spacing: 0.03em;
        }
        .sw-section-title {
            font-size: 1.05rem;
            font-weight: 700;
            color: #0F172A;
            margin-top: 0.75rem;
            margin-bottom: 0.5rem;
            padding-bottom: 0.3rem;
            border-bottom: 1px solid #E2E8F0;
        }
        .sw-note-banner {
            background: #EFF6FF;
            border-left: 4px solid #2563EB;
            padding: 0.65rem 0.95rem;
            border-radius: 6px;
            font-size: 0.85rem;
            color: #1E3A8A;
            margin-bottom: 0.9rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def status_pill_html(label: str) -> str:
    """Return an HTML status pill badge colored by domain status or severity."""
    key = str(label).upper().strip()
    bg, fg = STATUS_COLORS.get(key, ("#E2E8F0", "#1E293B"))
    safe_text = html.escape(str(label))
    return f'<span class="sw-pill" style="background:{bg};color:{fg};">{safe_text}</span>'


def render_page_header(title: str, subtitle: str, scope_label: str = "Scope: ALL WAREHOUSES") -> None:
    """Render the consistent top enterprise page banner."""
    safe_title = html.escape(title)
    safe_sub = html.escape(subtitle)
    safe_scope = html.escape(scope_label)
    st.markdown(
        f"""
        <div class="sw-header-card">
            <div class="sw-header-top">
                <h1 class="sw-header-title">{safe_title}</h1>
                <span class="sw-scope-chip">{safe_scope}</span>
            </div>
            <p class="sw-header-sub">{safe_sub}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_kpi_card(
    label: str,
    value: str,
    subtitle: str | None = None,
    accent: str = "blue",
) -> None:
    """Render an enterprise KPI card with a semantic left-border accent."""
    border_color = ACCENT_MAP.get(accent, ACCENT_MAP["blue"])
    safe_label = html.escape(label)
    safe_val = html.escape(value)
    sub_html = f'<div class="sw-kpi-delta">{html.escape(subtitle)}</div>' if subtitle else ""
    st.markdown(
        f"""
        <div class="sw-kpi-card" style="border-left: 4px solid {border_color};">
            <div class="sw-kpi-label">{safe_label}</div>
            <div class="sw-kpi-value">{safe_val}</div>
            {sub_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_section_header(title: str) -> None:
    """Render a subtle underlined section header."""
    st.markdown(f'<div class="sw-section-title">{html.escape(title)}</div>', unsafe_allow_html=True)


def render_telemetry_disclaimer(text: str) -> None:
    """Explicitly distinguish observed operational telemetry from simulated optimization estimates."""
    st.markdown(f'<div class="sw-note-banner">{html.escape(text)}</div>', unsafe_allow_html=True)


def render_api_unavailable_banner(err: APIUnavailableError, backend_url: str) -> None:
    """Gracefully handle FastAPI backend offline / unreachable errors without crashing."""
    st.error(
        f"**FastAPI Backend Unavailable** — Could not reach `{backend_url}` (`{err.error_code}`).",
        icon="🔌",
    )
    st.markdown(
        f"""
        **Diagnostic Details:** `{err.message}`

        To start the SlotWise AI FastAPI backend locally, run:
        ```powershell
        .\\.venv\\Scripts\\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
        ```
        Or verify the **Backend API URL** inside the left sidebar under **API & Operator Settings**.
        """
    )
    if st.button("Retry Backend Connection", type="primary", key="btn_retry_api_offline"):
        st.rerun()


def render_api_error(err: FrontendAPIError, title: str = "Backend Request Error") -> None:
    """Render structured 400 / 401 / 403 / 404 / 422 / 500 API errors cleanly."""
    if isinstance(err, APIUnavailableError):
        render_api_unavailable_banner(err, backend_url=str((err.details or {}).get("base_url", "")))
        return

    code_badge = f"[{err.status_code} {err.error_code}]" if err.status_code else f"[{err.error_code}]"
    st.error(f"**{title}** `{code_badge}`: {err.message}")
    if err.details:
        with st.expander("View Validation / Error Context"):
            st.json(err.details)


def render_empty_dataset_banner(custom_msg: str | None = None) -> None:
    """Gracefully handle empty databases where zero SKUs or locations are loaded."""
    msg = custom_msg or (
        "The operational database currently contains 0 warehouse locations or SKUs. "
        "Open **09 System Health** in the left navigation to generate and load a deterministic dataset profile (`small`, `medium`, or `full`)."
    )
    st.warning(f"**Empty Operational Dataset** — {msg}", icon="📦")
