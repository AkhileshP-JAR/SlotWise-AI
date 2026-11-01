"""HTTPX REST client connecting the Streamlit frontend strictly to the FastAPI backend."""

from __future__ import annotations

import os
from typing import Any

import httpx


class FrontendAPIError(Exception):
    """Base structured exception for FastAPI client errors."""

    def __init__(
        self,
        message: str,
        error_code: str = "API_ERROR",
        status_code: int | None = None,
        details: Any = None,
    ) -> None:
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.details = details
        super().__init__(message)


class APIUnavailableError(FrontendAPIError):
    """Raised when the FastAPI backend cannot be reached or times out."""

    def __init__(self, base_url: str, reason: str) -> None:
        super().__init__(
            message=f"Cannot connect to SlotWise AI Backend at {base_url}: {reason}",
            error_code="BACKEND_UNAVAILABLE",
            status_code=None,
            details={"base_url": base_url, "reason": reason},
        )


class APIHTTPError(FrontendAPIError):
    """Raised when the FastAPI backend returns a 4xx or 5xx structured ErrorResponse."""


_DEFAULT_TRANSPORT: httpx.BaseTransport | None = None


def set_default_transport(transport: httpx.BaseTransport | None) -> None:
    """Configure an optional custom HTTPX transport (used by automated Streamlit AppTest suites)."""
    global _DEFAULT_TRANSPORT
    _DEFAULT_TRANSPORT = transport


def get_default_backend_url() -> str:
    """Resolve the default FastAPI base URL from environment settings."""
    return os.getenv("SLOTWISE_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")


class SlotWiseAPIClient:
    """Typed synchronous HTTPX client wrapping `/health` and `/api/v1/*` endpoints."""

    def __init__(
        self,
        base_url: str | None = None,
        write_token: str = "",
        actor: str = "STREAMLIT_OPERATOR",
        timeout_seconds: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or get_default_backend_url()).rstrip("/")
        self.write_token = write_token.strip()
        self.actor = (actor.strip() or "STREAMLIT_OPERATOR")[:80]
        self.timeout_seconds = timeout_seconds
        self._transport = transport if transport is not None else _DEFAULT_TRANSPORT

    def _headers(self, is_write: bool = False) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/json",
            "X-Actor": self.actor,
        }
        if is_write and self.write_token:
            headers["X-API-Key"] = self.write_token
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        is_write: bool = False,
        timeout: float | None = None,
    ) -> Any:
        clean_params: dict[str, Any] | None = None
        if params:
            clean_params = {k: v for k, v in params.items() if v is not None and v != ""}

        client_kwargs: dict[str, Any] = {
            "base_url": self.base_url,
            "timeout": timeout or self.timeout_seconds,
        }
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        try:
            with httpx.Client(**client_kwargs) as client:
                resp = client.request(
                    method=method,
                    url=path,
                    params=clean_params,
                    json=json_body,
                    headers=self._headers(is_write=is_write),
                )
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError, httpx.RequestError) as exc:
            raise APIUnavailableError(base_url=self.base_url, reason=str(exc)) from exc

        if resp.status_code >= 400:
            err_code = f"HTTP_{resp.status_code}"
            msg = f"HTTP {resp.status_code} error calling {method} {path}"
            details: Any = None
            try:
                payload = resp.json()
                if isinstance(payload, dict):
                    err_code = str(payload.get("error_code", err_code))
                    msg = str(payload.get("message") or payload.get("detail") or msg)
                    details = payload.get("details")
            except ValueError:
                msg = resp.text[:400] or msg
            raise APIHTTPError(
                message=msg,
                error_code=err_code,
                status_code=resp.status_code,
                details=details,
            )

        return resp.json()

    # ------------------------------------------------------------------
    # Health & System
    # ------------------------------------------------------------------
    def get_health(self) -> dict[str, Any]:
        return self._request("GET", "/health", timeout=6.0)

    def get_system_status(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/system/status")

    def list_audit_events(
        self,
        entity_type: str | None = None,
        entity_id: str | None = None,
        event_type: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v1/system/audit-events",
            params={
                "entity_type": entity_type,
                "entity_id": entity_id,
                "event_type": event_type,
                "page": page,
                "page_size": page_size,
            },
        )

    # ------------------------------------------------------------------
    # Warehouses & Locations
    # ------------------------------------------------------------------
    def list_warehouses(
        self,
        status: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v1/warehouses",
            params={"status": status, "search": search, "page": page, "page_size": page_size},
        )

    def get_warehouse(self, warehouse_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/warehouses/{warehouse_id}")

    def list_locations(
        self,
        warehouse_id: str | None = None,
        zone_id: str | None = None,
        zone_type: str | None = None,
        temperature_type: str | None = None,
        storage_policy: str | None = None,
        accessibility_type: str | None = None,
        is_blocked: bool | None = None,
        occupied_only: bool | None = None,
        status: str | None = None,
        sort_by: str = "location_id",
        sort_order: str = "asc",
        page: int = 1,
        page_size: int = 250,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v1/locations",
            params={
                "warehouse_id": warehouse_id,
                "zone_id": zone_id,
                "zone_type": zone_type,
                "temperature_type": temperature_type,
                "storage_policy": storage_policy,
                "accessibility_type": accessibility_type,
                "is_blocked": is_blocked,
                "occupied_only": occupied_only,
                "status": status,
                "sort_by": sort_by,
                "sort_order": sort_order,
                "page": page,
                "page_size": page_size,
            },
        )

    def get_location(self, location_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/locations/{location_id}")

    # ------------------------------------------------------------------
    # SKUs, Profiles & Candidates
    # ------------------------------------------------------------------
    def list_skus(
        self,
        search: str | None = None,
        category: str | None = None,
        hazard_class: str | None = None,
        temperature_requirement: str | None = None,
        demand_velocity: str | None = None,
        abc_class: str | None = None,
        xyz_class: str | None = None,
        warehouse_id: str | None = None,
        status: str | None = None,
        sort_by: str = "sku_id",
        sort_order: str = "asc",
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v1/skus",
            params={
                "search": search,
                "category": category,
                "hazard_class": hazard_class,
                "temperature_requirement": temperature_requirement,
                "demand_velocity": demand_velocity,
                "abc_class": abc_class,
                "xyz_class": xyz_class,
                "warehouse_id": warehouse_id,
                "status": status,
                "sort_by": sort_by,
                "sort_order": sort_order,
                "page": page,
                "page_size": page_size,
            },
        )

    def get_sku(self, sku_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/skus/{sku_id}")

    def get_sku_profile(self, sku_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/skus/{sku_id}/profile")

    def get_sku_candidates(
        self,
        sku_id: str,
        warehouse_id: str | None = None,
        zone_id: str | None = None,
        storage_policy_override: str | None = None,
        top_n: int = 15,
        max_rejected_samples: int = 15,
        explicit_quantity: int | None = None,
        vacate_current_slot: bool = True,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/v1/skus/{sku_id}/candidates",
            params={
                "warehouse_id": warehouse_id,
                "zone_id": zone_id,
                "storage_policy_override": storage_policy_override,
                "top_n": top_n,
                "max_rejected_samples": max_rejected_samples,
                "explicit_quantity": explicit_quantity,
                "vacate_current_slot": vacate_current_slot,
            },
        )

    # ------------------------------------------------------------------
    # Data Pipeline
    # ------------------------------------------------------------------
    def generate_data(
        self,
        profile: str = "small",
        seed: int = 20261022,
        inject_anomalies: bool = True,
        load_into_database: bool = True,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/data/generate",
            json_body={
                "profile": profile,
                "seed": seed,
                "inject_anomalies": inject_anomalies,
                "load_into_database": load_into_database,
            },
            is_write=True,
            timeout=180.0,
        )

    def validate_data(self, profile: str = "small") -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/data/validate",
            json_body={"profile": profile},
            is_write=True,
            timeout=120.0,
        )

    def get_data_manifest(self, profile: str | None = None) -> dict[str, Any]:
        return self._request("GET", "/api/v1/data/manifest", params={"profile": profile})

    # ------------------------------------------------------------------
    # Slotting Optimization
    # ------------------------------------------------------------------
    def run_optimization(
        self,
        warehouse_id: str | None = None,
        zone_id: str | None = None,
        sku_ids: list[str] | None = None,
        strategy: str = "auto",
        storage_policy_override: str | None = None,
        preserve_warehouse_affinity: bool = True,
        apply_immediately: bool = False,
        use_ml_forecasts: bool = False,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/optimization/run",
            json_body={
                "warehouse_id": warehouse_id,
                "zone_id": zone_id,
                "sku_ids": sku_ids,
                "strategy": strategy,
                "storage_policy_override": storage_policy_override,
                "preserve_warehouse_affinity": preserve_warehouse_affinity,
                "apply_immediately": apply_immediately,
                "use_ml_forecasts": use_ml_forecasts,
            },
            is_write=True,
            timeout=120.0,
        )

    def list_optimization_runs(
        self,
        warehouse_scope: str | None = None,
        algorithm_name: str | None = None,
        run_status: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v1/optimization/runs",
            params={
                "warehouse_scope": warehouse_scope,
                "algorithm_name": algorithm_name,
                "run_status": run_status,
                "page": page,
                "page_size": page_size,
            },
        )

    def get_optimization_run(self, run_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/optimization/runs/{run_id}")

    def list_run_assignments(
        self,
        run_id: str,
        warehouse_id: str | None = None,
        sku_id: str | None = None,
        assignment_status: str | None = None,
        include_reasons: bool = False,
        sort_by: str = "assignment_score",
        sort_order: str = "desc",
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/v1/optimization/runs/{run_id}/assignments",
            params={
                "warehouse_id": warehouse_id,
                "sku_id": sku_id,
                "assignment_status": assignment_status,
                "include_reasons": include_reasons,
                "sort_by": sort_by,
                "sort_order": sort_order,
                "page": page,
                "page_size": page_size,
            },
        )

    def approve_run_assignments(
        self,
        run_id: str,
        assignment_ids: list[str] | None = None,
        auto_resolve_exceptions: bool = True,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v1/optimization/runs/{run_id}/approve",
            json_body={
                "assignment_ids": assignment_ids,
                "auto_resolve_exceptions": auto_resolve_exceptions,
            },
            is_write=True,
        )

    def reject_run_assignments(
        self,
        run_id: str,
        reason: str = "Rejected during operator dashboard review.",
        assignment_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v1/optimization/runs/{run_id}/reject",
            json_body={"assignment_ids": assignment_ids, "reason": reason},
            is_write=True,
        )

    def list_assignment_history(
        self,
        warehouse_id: str | None = None,
        sku_id: str | None = None,
        location_id: str | None = None,
        assignment_status: str | None = None,
        include_reasons: bool = False,
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v1/optimization/assignments/history",
            params={
                "warehouse_id": warehouse_id,
                "sku_id": sku_id,
                "location_id": location_id,
                "assignment_status": assignment_status,
                "include_reasons": include_reasons,
                "page": page,
                "page_size": page_size,
            },
        )

    # ------------------------------------------------------------------
    # Picking & Replenishment
    # ------------------------------------------------------------------
    def simulate_picking(
        self,
        warehouse_id: str | None = None,
        optimization_run_id: str | None = None,
        order_ids: list[str] | None = None,
        max_orders: int = 300,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/picking/simulate",
            json_body={
                "warehouse_id": warehouse_id,
                "optimization_run_id": optimization_run_id,
                "order_ids": order_ids,
                "max_orders": max_orders,
            },
        )

    def get_picking_analytics(
        self,
        warehouse_id: str | None = None,
        optimization_run_id: str | None = None,
        max_orders: int = 250,
        recent_orders_limit: int = 40,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v1/picking/analytics",
            params={
                "warehouse_id": warehouse_id,
                "optimization_run_id": optimization_run_id,
                "max_orders": max_orders,
                "recent_orders_limit": recent_orders_limit,
            },
        )

    def simulate_replenishment(
        self,
        warehouse_id: str | None = None,
        optimization_run_id: str | None = None,
        use_baseline_assignments: bool = False,
        labor_capacity_multiplier: float = 1.0,
        persist_events: bool = False,
        use_ml_forecasts: bool = False,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/replenishment/simulate",
            json_body={
                "warehouse_id": warehouse_id,
                "optimization_run_id": optimization_run_id,
                "use_baseline_assignments": use_baseline_assignments,
                "labor_capacity_multiplier": labor_capacity_multiplier,
                "persist_events": persist_events,
                "use_ml_forecasts": use_ml_forecasts,
            },
            is_write=True,
        )

    def get_replenishment_analytics(
        self,
        warehouse_id: str | None = None,
        priority: str | None = None,
        replenishment_status: str | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v1/replenishment/analytics",
            params={
                "warehouse_id": warehouse_id,
                "priority": priority,
                "replenishment_status": replenishment_status,
                "page": page,
                "page_size": page_size,
            },
        )

    # ------------------------------------------------------------------
    # Exceptions
    # ------------------------------------------------------------------
    def list_exceptions(
        self,
        category: str | None = None,
        issue_type: str | None = None,
        severity: str | None = None,
        resolution_status: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        optimization_run_id: str | None = None,
        include_ground_truth_eval: bool = False,
        sort_by: str = "exception_id",
        sort_order: str = "asc",
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v1/exceptions",
            params={
                "category": category,
                "issue_type": issue_type,
                "severity": severity,
                "resolution_status": resolution_status,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "optimization_run_id": optimization_run_id,
                "include_ground_truth_eval": include_ground_truth_eval,
                "sort_by": sort_by,
                "sort_order": sort_order,
                "page": page,
                "page_size": page_size,
            },
        )

    def scan_exceptions(
        self,
        optimization_run_id: str | None = None,
        auto_resolve_remediated: bool = True,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/exceptions/scan",
            json_body={
                "optimization_run_id": optimization_run_id,
                "auto_resolve_remediated": auto_resolve_remediated,
            },
            is_write=True,
        )

    def get_exception(self, exception_id: str, include_ground_truth_eval: bool = False) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/v1/exceptions/{exception_id}",
            params={"include_ground_truth_eval": include_ground_truth_eval},
        )

    def resolve_exception(
        self,
        exception_id: str,
        resolution_status: str,
        resolution_note: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "PATCH",
            f"/api/v1/exceptions/{exception_id}/resolve",
            json_body={
                "resolution_status": resolution_status,
                "resolution_note": resolution_note,
            },
            is_write=True,
        )

    # ------------------------------------------------------------------
    # Analytics, Scenarios & Decision Assistant
    # ------------------------------------------------------------------
    def get_analytics_overview(
        self,
        warehouse_id: str | None = None,
        sample_picking_orders: int = 250,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v1/analytics/overview",
            params={
                "warehouse_id": warehouse_id,
                "sample_picking_orders": sample_picking_orders,
            },
        )

    def get_warehouse_analytics(
        self,
        warehouse_id: str,
        sample_picking_orders: int = 250,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/v1/analytics/warehouse/{warehouse_id}",
            params={"sample_picking_orders": sample_picking_orders},
        )

    def get_optimization_analytics(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/analytics/optimization")

    def create_scenario(
        self,
        optimization_run_id: str,
        scenario_name: str | None = None,
        description: str = "",
        warehouse_id: str | None = None,
        max_orders: int = 400,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/scenarios",
            json_body={
                "optimization_run_id": optimization_run_id,
                "scenario_name": scenario_name,
                "description": description,
                "warehouse_id": warehouse_id,
                "max_orders": max_orders,
            },
            is_write=True,
        )

    def list_scenarios(
        self,
        warehouse_id: str | None = None,
        optimization_run_id: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v1/scenarios",
            params={
                "warehouse_id": warehouse_id,
                "optimization_run_id": optimization_run_id,
                "page": page,
                "page_size": page_size,
            },
        )

    def get_scenario_comparison(self, scenario_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/scenarios/{scenario_id}/comparison")

    def query_assistant(self, question: str, warehouse_id: str | None = None) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/assistant/query",
            json_body={"question": question, "warehouse_id": warehouse_id},
        )

    # ------------------------------------------------------------------
    # ML Demand Forecasting & MLflow
    # ------------------------------------------------------------------
    def get_ml_status(self, profile: str | None = None) -> dict[str, Any]:
        return self._request("GET", "/api/v1/ml/status", params={"profile": profile})

    def train_ml_forecaster(
        self,
        profile: str | None = None,
        forecast_horizon_days: int = 14,
        train_ratio: float = 0.80,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/ml/train",
            json_body={
                "profile": profile,
                "forecast_horizon_days": forecast_horizon_days,
                "train_ratio": train_ratio,
            },
            is_write=True,
            timeout=180.0,
        )

    def list_ml_forecasts(
        self,
        sku_id: str | None = None,
        warehouse_id: str | None = None,
        velocity_shift_only: bool = False,
        surging_only: bool = False,
        sort_by: str = "surge_ratio",
        sort_order: str = "desc",
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v1/ml/forecasts",
            params={
                "sku_id": sku_id,
                "warehouse_id": warehouse_id,
                "velocity_shift_only": velocity_shift_only,
                "surging_only": surging_only,
                "sort_by": sort_by,
                "sort_order": sort_order,
                "page": page,
                "page_size": page_size,
            },
        )
