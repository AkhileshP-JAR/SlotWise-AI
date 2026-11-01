"""Comprehensive Phase 5 FastAPI REST endpoint integration tests using HTTPX ASGI transport."""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config import DATA_DIR, EnvSettings
from backend.app.database import create_db_engine
from backend.app.dependencies import (
    get_pipeline_service,
    get_read_db,
    get_settings,
    get_write_db,
)
from backend.app.main import create_app
from backend.app.services.data_loader import DatabaseLoader
from backend.app.services.pipeline_service import DataPipelineService


class HttpxASGITestClient:
    """Synchronous test harness driving genuine `httpx.AsyncClient` requests over `ASGITransport`."""

    def __init__(self, app: Any, base_url: str = "http://testserver") -> None:
        self.app = app
        self.base_url = base_url

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        async def _send() -> httpx.Response:
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url=self.base_url) as client:
                return await client.request(method, url, **kwargs)

        return asyncio.run(_send())

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", url, **kwargs)


@pytest.fixture(scope="module")
def api_test_ctx(tmp_path_factory: pytest.TempPathFactory) -> Generator[dict[str, Any], None, None]:
    """Provision an isolated SQLite database loaded with the `small` dataset and return HTTPX ASGI client."""
    tmp_dir = tmp_path_factory.mktemp("phase5_api")
    db_path = tmp_dir / "phase5_api.db"
    engine = create_db_engine(f"sqlite:///{db_path.as_posix()}")

    loader = DatabaseLoader(engine=engine)
    loader.load_profile_from_disk(profile="small", data_root=DATA_DIR, reset_existing=True)

    testing_session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )

    app = create_app()

    def _override_read_db() -> Generator[Session, None, None]:
        s = testing_session_factory()
        try:
            yield s
        finally:
            s.close()

    def _override_write_db() -> Generator[Session, None, None]:
        s = testing_session_factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def _override_settings() -> EnvSettings:
        return EnvSettings(
            env="test",
            database_url=f"sqlite:///{db_path.as_posix()}",
            config_profile="small",
            api_write_token="",
        )

    def _override_pipeline() -> DataPipelineService:
        return DataPipelineService(data_root=DATA_DIR, engine=engine)

    app.dependency_overrides[get_read_db] = _override_read_db
    app.dependency_overrides[get_write_db] = _override_write_db
    app.dependency_overrides[get_settings] = _override_settings
    app.dependency_overrides[get_pipeline_service] = _override_pipeline

    client = HttpxASGITestClient(app=app)
    yield {
        "client": client,
        "app": app,
        "engine": engine,
        "db_path": db_path,
        "default_settings_override": _override_settings,
        "default_pipeline_override": _override_pipeline,
    }
    engine.dispose()


def test_health_openapi_and_system_status(api_test_ctx: dict[str, Any]) -> None:
    client: HttpxASGITestClient = api_test_ctx["client"]

    health_resp = client.get("/health")
    assert health_resp.status_code == 200
    h_json = health_resp.json()
    assert h_json["status"] == "HEALTHY"
    assert h_json["database_connected"] is True
    assert h_json["service"] == "SlotWise AI"

    openapi_resp = client.get("/openapi.json")
    assert openapi_resp.status_code == 200
    paths = openapi_resp.json()["paths"]
    for required_path in [
        "/health",
        "/api/v1/system/status",
        "/api/v1/warehouses",
        "/api/v1/locations",
        "/api/v1/skus",
        "/api/v1/skus/{sku_id}/candidates",
        "/api/v1/data/generate",
        "/api/v1/data/validate",
        "/api/v1/data/manifest",
        "/api/v1/optimization/run",
        "/api/v1/picking/simulate",
        "/api/v1/replenishment/simulate",
        "/api/v1/exceptions",
        "/api/v1/analytics/overview",
        "/api/v1/scenarios",
        "/api/v1/assistant/query",
    ]:
        assert required_path in paths

    docs_resp = client.get("/docs")
    assert docs_resp.status_code == 200
    assert "Swagger UI" in docs_resp.text

    status_resp = client.get("/api/v1/system/status")
    assert status_resp.status_code == 200
    s_json = status_resp.json()
    assert s_json["status"] == "HEALTHY"
    assert s_json["active_profile"] == "small"
    assert len(s_json["table_counts"]) == 21
    assert s_json["table_counts"]["skus"] == 250
    assert s_json["table_counts"]["locations"] == 500
    assert s_json["total_records"] > 8000


def test_warehouses_and_locations_filtering_sorting_pagination(api_test_ctx: dict[str, Any]) -> None:
    client: HttpxASGITestClient = api_test_ctx["client"]

    wh_list = client.get("/api/v1/warehouses")
    assert wh_list.status_code == 200
    wh_payload = wh_list.json()
    assert wh_payload["meta"]["total_items"] == 2
    assert [w["warehouse_id"] for w in wh_payload["items"]] == ["WH-001", "WH-002"]
    assert sum(w["total_locations"] for w in wh_payload["items"]) == 500

    wh_detail = client.get("/api/v1/warehouses/WH-001")
    assert wh_detail.status_code == 200
    wd = wh_detail.json()
    assert wd["warehouse_id"] == "WH-001"
    assert len(wd["zones"]) == 7
    assert len(wd["aisles"]) >= 14
    assert any("FLAMMABLE" in z["permitted_hazards"] for z in wd["zones"])

    wh_404 = client.get("/api/v1/warehouses/WH-999")
    assert wh_404.status_code == 404
    assert wh_404.json()["error_code"] == "ENTITY_NOT_FOUND"

    loc_resp = client.get(
        "/api/v1/locations",
        params={
            "warehouse_id": "WH-001",
            "zone_type": "FAST_PICK",
            "is_blocked": False,
            "sort_by": "max_weight_kg",
            "sort_order": "desc",
            "page": 1,
            "page_size": 10,
        },
    )
    assert loc_resp.status_code == 200
    loc_data = loc_resp.json()
    assert len(loc_data["items"]) == 10
    assert loc_data["meta"]["total_items"] > 10
    for item in loc_data["items"]:
        assert item["warehouse_id"] == "WH-001"
        assert item["zone_type"] == "FAST_PICK"
        assert item["is_blocked"] is False
        assert item["aisle_is_blocked"] is False

    first_loc_id = loc_data["items"][0]["location_id"]
    loc_detail = client.get(f"/api/v1/locations/{first_loc_id}")
    assert loc_detail.status_code == 200
    assert loc_detail.json()["location_id"] == first_loc_id
    assert isinstance(loc_detail.json()["allowed_hazard_classes"], list)


def test_skus_profiles_and_explainable_candidates(api_test_ctx: dict[str, Any]) -> None:
    client: HttpxASGITestClient = api_test_ctx["client"]

    sku_list = client.get(
        "/api/v1/skus",
        params={
            "demand_velocity": "FAST",
            "sort_by": "pick_frequency",
            "sort_order": "desc",
            "page": 1,
            "page_size": 10,
        },
    )
    assert sku_list.status_code == 200
    s_data = sku_list.json()
    assert len(s_data["items"]) == 10
    freqs = [item["pick_frequency"] for item in s_data["items"]]
    assert freqs == sorted(freqs, reverse=True)
    assert all(item["demand_velocity"] == "FAST" for item in s_data["items"])

    target_sku = s_data["items"][0]["sku_id"]
    sku_detail = client.get(f"/api/v1/skus/{target_sku}")
    assert sku_detail.status_code == 200
    sd = sku_detail.json()
    assert sd["sku_id"] == target_sku
    assert sd["profile"] is not None
    assert len(sd["demand_trend"]) > 0

    prof_resp = client.get(f"/api/v1/skus/{target_sku}/profile")
    assert prof_resp.status_code == 200
    assert prof_resp.json()["sku_id"] == target_sku

    cand_resp = client.get(
        f"/api/v1/skus/{target_sku}/candidates",
        params={"top_n": 5, "max_rejected_samples": 8},
    )
    assert cand_resp.status_code == 200
    cd = cand_resp.json()
    assert cd["total_locations_evaluated"] == 500
    assert cd["total_feasible_count"] + cd["total_rejected_count"] == 500
    assert len(cd["top_feasible"]) == 5
    assert [c["rank_position"] for c in cd["top_feasible"]] == [1, 2, 3, 4, 5]
    assert cd["top_feasible"][0]["total_score"] >= cd["top_feasible"][-1]["total_score"]
    assert cd["total_rejected_count"] > 0
    assert all(len(r["rejection_reasons"]) >= 1 for r in cd["rejected_samples"])


def test_exceptions_scan_filtering_and_lifecycle_resolution(api_test_ctx: dict[str, Any]) -> None:
    client: HttpxASGITestClient = api_test_ctx["client"]

    scan_resp = client.post("/api/v1/exceptions/scan", json={"auto_resolve_remediated": False})
    assert scan_resp.status_code == 200
    scan_json = scan_resp.json()
    assert scan_json["active_findings_count"] == 21
    assert scan_json["newly_created_count"] == 21

    exc_list = client.get(
        "/api/v1/exceptions",
        params={"severity": "CRITICAL", "include_ground_truth_eval": True},
    )
    assert exc_list.status_code == 200
    e_data = exc_list.json()
    assert e_data["meta"]["total_items"] == 7  # 4 CAPACITY_EXCEEDED + 3 INCOMPATIBLE_COLOCATION
    assert all(item["ground_truth_flag"] is True for item in e_data["items"])

    # Pick a BLOCKED_AISLE_PICK_PATH exception to test manual operator lifecycle transitions
    blk_list = client.get("/api/v1/exceptions", params={"issue_type": "BLOCKED_AISLE_PICK_PATH"})
    blk_exc_id = blk_list.json()["items"][0]["exception_id"]

    single_exc = client.get(f"/api/v1/exceptions/{blk_exc_id}")
    assert single_exc.status_code == 200
    assert single_exc.json()["resolution_status"] == "OPEN"

    # OPEN -> IN_REVIEW
    rev_resp = client.patch(
        f"/api/v1/exceptions/{blk_exc_id}/resolve",
        json={"resolution_status": "IN_REVIEW", "resolution_note": "Investigating blocked aisle detour."},
    )
    assert rev_resp.status_code == 200
    assert rev_resp.json()["resolution_status"] == "IN_REVIEW"

    # IN_REVIEW -> RESOLVED
    res_resp = client.patch(
        f"/api/v1/exceptions/{blk_exc_id}/resolve",
        json={"resolution_status": "RESOLVED", "resolution_note": "Cleared obstruction and updated route."},
    )
    assert res_resp.status_code == 200
    assert res_resp.json()["resolution_status"] == "RESOLVED"
    assert res_resp.json()["resolved_at"] is not None

    # Illegal transition RESOLVED -> DISMISSED must return 400 DOMAIN_VALIDATION_ERROR
    bad_trans = client.patch(
        f"/api/v1/exceptions/{blk_exc_id}/resolve",
        json={"resolution_status": "DISMISSED", "resolution_note": "Illegal jump."},
    )
    assert bad_trans.status_code == 400
    assert bad_trans.json()["error_code"] == "DOMAIN_VALIDATION_ERROR"


def test_optimization_governance_reject_approve_and_history(api_test_ctx: dict[str, Any]) -> None:
    client: HttpxASGITestClient = api_test_ctx["client"]

    # 1. Partial 3-SKU run -> PROPOSED -> REJECTED (leaves existing ACTIVE assignments untouched)
    run1_resp = client.post(
        "/api/v1/optimization/run",
        json={
            "sku_ids": ["SKU-000001", "SKU-000002", "SKU-000003"],
            "strategy": "greedy",
            "apply_immediately": False,
        },
    )
    assert run1_resp.status_code == 200
    run1_id = run1_resp.json()["run_id"]
    assert run1_resp.json()["assignment_count"] == 3

    rej_resp = client.post(
        f"/api/v1/optimization/runs/{run1_id}/reject",
        json={"reason": "Testing governance rejection path."},
    )
    assert rej_resp.status_code == 200
    assert rej_resp.json()["rejected_count"] == 3

    # 2. Full warehouse OR-Tools CP-SAT optimization -> PROPOSED -> APPROVE
    run2_resp = client.post(
        "/api/v1/optimization/run",
        json={
            "strategy": "ortools_cpsat",
            "preserve_warehouse_affinity": True,
            "apply_immediately": False,
        },
    )
    assert run2_resp.status_code == 200
    run2 = run2_resp.json()
    run2_id = run2["run_id"]
    assert run2["algorithm_name"] == "ORTOOLS_CPSAT"
    assert run2["assignment_count"] == 248
    assert run2["unassigned_sku_count"] == 2
    assert run2["kpi_results_json"]["constraint_violation_count"] == 0

    assns_resp = client.get(
        f"/api/v1/optimization/runs/{run2_id}/assignments",
        params={"include_reasons": True, "page": 1, "page_size": 5},
    )
    assert assns_resp.status_code == 200
    assns_json = assns_resp.json()
    assert assns_json["meta"]["total_items"] == 248
    assert all(a["assignment_status"] == "PROPOSED" for a in assns_json["items"])
    assert all(len(a["candidate_reasons"]) >= 1 for a in assns_json["items"])

    app_resp = client.post(
        f"/api/v1/optimization/runs/{run2_id}/approve",
        json={"auto_resolve_exceptions": True},
    )
    assert app_resp.status_code == 200
    app_json = app_resp.json()
    assert app_json["approved_count"] == 248
    assert app_json["superseded_count"] == 248
    assert app_json["exception_rescan"]["auto_resolved_count"] == 14

    # Verify non-destructive historical assignments remain intact as SUPERSEDED and REJECTED
    hist_sup = client.get(
        "/api/v1/optimization/assignments/history",
        params={"assignment_status": "SUPERSEDED", "page_size": 10},
    )
    assert hist_sup.status_code == 200
    assert hist_sup.json()["meta"]["total_items"] == 248

    hist_rej = client.get(
        "/api/v1/optimization/assignments/history",
        params={"assignment_status": "REJECTED"},
    )
    assert hist_rej.status_code == 200
    assert hist_rej.json()["meta"]["total_items"] == 3


def test_picking_and_replenishment_simulation_and_analytics(api_test_ctx: dict[str, Any]) -> None:
    client: HttpxASGITestClient = api_test_ctx["client"]

    pick_sim = client.post("/api/v1/picking/simulate", json={"max_orders": 250})
    assert pick_sim.status_code == 200
    ps = pick_sim.json()
    assert ps["orders_evaluated"] > 200
    assert ps["optimized_distance_m"] < ps["baseline_distance_m"]
    assert ps["percentage_improvement"] > 15.0
    assert len(ps["sample_orders"]) > 0

    pick_ana = client.get("/api/v1/picking/analytics", params={"warehouse_id": "WH-001", "max_orders": 150})
    assert pick_ana.status_code == 200
    pa = pick_ana.json()
    assert pa["warehouse_scope"] == "WH-001"
    assert pa["observed_telemetry"]["total_pick_events"] > 0
    assert len(pa["recent_orders"]) > 0

    rep_sim = client.post(
        "/api/v1/replenishment/simulate",
        json={"labor_capacity_multiplier": 0.4, "persist_events": False},
    )
    assert rep_sim.status_code == 200
    rs = rep_sim.json()
    assert rs["total_requests"] > 0
    assert len(rs["warehouse_breakdown"]) == 2

    rep_ana = client.get("/api/v1/replenishment/analytics")
    assert rep_ana.status_code == 200
    ra = rep_ana.json()
    assert ra["total_tasks"] > 0
    assert any(w["is_overloaded"] is True for w in ra["warehouse_labor_summary"])


def test_scenarios_analytics_assistant_and_audit_trail(api_test_ctx: dict[str, Any]) -> None:
    client: HttpxASGITestClient = api_test_ctx["client"]

    runs = client.get("/api/v1/optimization/runs").json()["items"]
    full_run_id = next(r["run_id"] for r in runs if r["assignment_count"] == 248)

    scn_create = client.post(
        "/api/v1/scenarios",
        json={
            "optimization_run_id": full_run_id,
            "scenario_name": "Phase 5 API Benchmark Scenario",
            "max_orders": 300,
        },
    )
    assert scn_create.status_code == 201
    scn = scn_create.json()
    scn_id = scn["scenario_id"]
    assert scn["comparison_delta_json"]["pick_distance_saved_m"] > 0
    assert scn["comparison_delta_json"]["capacity_violations_eliminated"] == 4

    scn_list = client.get("/api/v1/scenarios")
    assert scn_list.status_code == 200
    assert scn_list.json()["meta"]["total_items"] >= 1

    scn_cmp = client.get(f"/api/v1/scenarios/{scn_id}/comparison")
    assert scn_cmp.status_code == 200
    assert scn_cmp.json()["scenario_id"] == scn_id

    ov_resp = client.get("/api/v1/analytics/overview")
    assert ov_resp.status_code == 200
    ov = ov_resp.json()
    assert ov["ground_truth_evaluation"]["f1_score"] == 1.0
    assert ov["picking_simulated_estimates"]["distance_improvement_pct"] > 15.0

    wh_ana_1 = client.get("/api/v1/analytics/warehouse/WH-001")
    assert wh_ana_1.status_code == 200
    assert len(wh_ana_1.json()["zone_utilization"]) == 7

    wh_ana_2 = client.get("/api/v1/analytics/warehouse/WH-002")
    assert wh_ana_2.status_code == 200
    assert wh_ana_2.json()["aisle_summary"]["blocked_aisles"] >= 1

    opt_ana = client.get("/api/v1/analytics/optimization")
    assert opt_ana.status_code == 200
    assert opt_ana.json()["total_runs"] >= 2
    assert opt_ana.json()["average_active_score_components"]["assignment_score"] > 0.6

    asst_resp = client.post(
        "/api/v1/assistant/query",
        json={"question": "Why was SKU-000001 placed in its top candidate slot in WH-001?"},
    )
    assert asst_resp.status_code == 200
    aj = asst_resp.json()
    assert aj["grounding_verified"] is True
    assert len(aj["trace"]) == 6
    assert "SKU-000001" in aj["cited_entity_ids"]

    audit_resp = client.get("/api/v1/system/audit-events")
    assert audit_resp.status_code == 200
    assert audit_resp.json()["meta"]["total_items"] >= 5


def test_data_pipeline_endpoints_validation_errors_and_write_auth(
    api_test_ctx: dict[str, Any],
    tmp_path: Path,
) -> None:
    client: HttpxASGITestClient = api_test_ctx["client"]
    app = api_test_ctx["app"]

    manifest_resp = client.get("/api/v1/data/manifest", params={"profile": "small"})
    assert manifest_resp.status_code == 200
    assert manifest_resp.json()["available"] is True
    assert manifest_resp.json()["manifest"]["seed"] == 20261022

    val_resp = client.post("/api/v1/data/validate", json={"profile": "small"})
    assert val_resp.status_code == 200
    assert val_resp.json()["is_valid"] is True

    # Test POST /api/v1/data/generate into an isolated scratch output directory
    gen_scratch = tmp_path / "gen_test_data"
    app.dependency_overrides[get_pipeline_service] = lambda: DataPipelineService(
        data_root=gen_scratch, engine=api_test_ctx["engine"]
    )
    try:
        gen_resp = client.post(
            "/api/v1/data/generate",
            json={
                "profile": "small",
                "seed": 20261022,
                "inject_anomalies": True,
                "load_into_database": False,
            },
        )
        assert gen_resp.status_code == 200
        gj = gen_resp.json()
        assert gj["status"] == "SUCCESS"
        assert gj["entity_counts"]["skus"] == 250
        assert gj["ground_truth_anomaly_count"] == 21
    finally:
        app.dependency_overrides[get_pipeline_service] = api_test_ctx["default_pipeline_override"]

    # Test 422 RequestValidationError structured payload
    bad_req = client.post("/api/v1/picking/simulate", json={"max_orders": 0})
    assert bad_req.status_code == 422
    assert bad_req.json()["error_code"] == "REQUEST_VALIDATION_ERROR"

    # Test write-token authorization safeguard (401 missing, 403 invalid, 200 valid X-API-Key & Bearer)
    app.dependency_overrides[get_settings] = lambda: EnvSettings(
        env="test",
        database_url=f"sqlite:///{api_test_ctx['db_path'].as_posix()}",
        config_profile="small",
        api_write_token="slotwise-prod-write-secret",
    )
    try:
        unauth = client.post("/api/v1/exceptions/scan")
        assert unauth.status_code == 401
        assert unauth.json()["error_code"] == "HTTP_401"

        forbid = client.post("/api/v1/exceptions/scan", headers={"X-API-Key": "wrong-token"})
        assert forbid.status_code == 403
        assert forbid.json()["error_code"] == "HTTP_403"

        ok_key = client.post(
            "/api/v1/exceptions/scan",
            headers={"X-API-Key": "slotwise-prod-write-secret", "X-Actor": "QA_REVIEWER"},
        )
        assert ok_key.status_code == 200

        ok_bearer = client.post(
            "/api/v1/exceptions/scan",
            headers={"Authorization": "Bearer slotwise-prod-write-secret"},
        )
        assert ok_bearer.status_code == 200
    finally:
        app.dependency_overrides[get_settings] = api_test_ctx["default_settings_override"]
