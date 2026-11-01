"""Phase 6 Streamlit Frontend end-to-end integration tests using Streamlit AppTest + FastAPI ASGI."""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session, sessionmaker
from streamlit.testing.v1 import AppTest

from backend.app.config import DATA_DIR, PROJECT_ROOT, EnvSettings
from backend.app.database import Base, create_db_engine
from backend.app.dependencies import (
    get_pipeline_service,
    get_read_db,
    get_settings,
    get_write_db,
)
from backend.app.main import create_app
from backend.app.services.data_loader import DatabaseLoader
from backend.app.services.pipeline_service import DataPipelineService
from frontend.components.api_client import set_default_transport

FRONTEND_DIR = PROJECT_ROOT / "frontend"
ALL_PAGE_FILES: list[str] = [
    str(FRONTEND_DIR / "app.py"),
    str(FRONTEND_DIR / "pages" / "01_Overview.py"),
    str(FRONTEND_DIR / "pages" / "02_Warehouse_Explorer.py"),
    str(FRONTEND_DIR / "pages" / "03_Inventory.py"),
    str(FRONTEND_DIR / "pages" / "04_Slotting_Optimizer.py"),
    str(FRONTEND_DIR / "pages" / "05_Picking_Analytics.py"),
    str(FRONTEND_DIR / "pages" / "06_Replenishment.py"),
    str(FRONTEND_DIR / "pages" / "07_Exceptions.py"),
    str(FRONTEND_DIR / "pages" / "08_Scenario_Comparison.py"),
    str(FRONTEND_DIR / "pages" / "09_System_Health.py"),
]


class SyncFastAPITransport(httpx.BaseTransport):
    """Synchronous HTTPX transport bridging `SlotWiseAPIClient` directly to an in-process FastAPI app."""

    def __init__(self, app: Any) -> None:
        self._app = app

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        async def _dispatch() -> tuple[int, httpx.Headers, bytes]:
            transport = httpx.ASGITransport(app=self._app)
            async with httpx.AsyncClient(transport=transport) as client:
                resp = await client.request(
                    method=request.method,
                    url=str(request.url),
                    headers=request.headers,
                    content=request.content,
                )
                return resp.status_code, resp.headers, resp.content

        status_code, headers, body = asyncio.run(_dispatch())
        return httpx.Response(
            status_code=status_code,
            headers=headers,
            content=body,
            request=request,
        )


class OfflineTransport(httpx.BaseTransport):
    """Transport simulating an unreachable FastAPI backend (`httpx.ConnectError`)."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Simulated connection refused to FastAPI server", request=request)


def _build_fastapi_app_for_db(db_path: Path) -> tuple[Any, Any]:
    engine = create_db_engine(f"sqlite:///{db_path.as_posix()}")
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )
    app = create_app()

    def _override_read_db() -> Generator[Session, None, None]:
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    def _override_write_db() -> Generator[Session, None, None]:
        s = session_factory()
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
            llm_provider="none",
        )

    def _override_pipeline() -> DataPipelineService:
        return DataPipelineService(engine=engine, data_root=DATA_DIR)

    app.dependency_overrides[get_read_db] = _override_read_db
    app.dependency_overrides[get_write_db] = _override_write_db
    app.dependency_overrides[get_settings] = _override_settings
    app.dependency_overrides[get_pipeline_service] = _override_pipeline
    return app, engine


@pytest.fixture(scope="module")
def populated_frontend_transport(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[SyncFastAPITransport, None, None]:
    """Provision an isolated SQLite DB with `small` data, exception scan, optimization run, and scenario."""
    tmp_dir = tmp_path_factory.mktemp("phase6_frontend")
    db_path = tmp_dir / "phase6_frontend.db"
    app, engine = _build_fastapi_app_for_db(db_path)

    loader = DatabaseLoader(engine=engine)
    loader.load_profile_from_disk(profile="small", data_root=DATA_DIR, reset_existing=True)

    transport = SyncFastAPITransport(app)
    with httpx.Client(transport=transport, base_url="http://testserver") as seed_client:
        assert seed_client.post("/api/v1/exceptions/scan").status_code == 200
        opt_resp = seed_client.post(
            "/api/v1/optimization/run",
            json={"strategy": "greedy", "apply_immediately": True},
        )
        assert opt_resp.status_code == 200
        run_id = opt_resp.json()["run_id"]
        scn_resp = seed_client.post(
            "/api/v1/scenarios",
            json={"optimization_run_id": run_id, "max_orders": 120},
        )
        assert scn_resp.status_code == 201

    yield transport
    set_default_transport(None)
    engine.dispose()


@pytest.fixture(scope="module")
def empty_frontend_transport(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[SyncFastAPITransport, None, None]:
    """Provision an initialized 21-table SQLite database containing 0 operational records."""
    tmp_dir = tmp_path_factory.mktemp("phase6_empty")
    db_path = tmp_dir / "phase6_empty.db"
    app, engine = _build_fastapi_app_for_db(db_path)
    Base.metadata.create_all(bind=engine)

    transport = SyncFastAPITransport(app)
    yield transport
    set_default_transport(None)
    engine.dispose()


@pytest.mark.parametrize("page_file", ALL_PAGE_FILES)
def test_all_streamlit_pages_render_live_backend_data(
    page_file: str,
    populated_frontend_transport: SyncFastAPITransport,
) -> None:
    """Verify `frontend/app.py` and all 9 dashboard pages render real backend data with zero unhandled exceptions."""
    set_default_transport(populated_frontend_transport)
    try:
        at = AppTest.from_file(page_file, default_timeout=60)
        at.run()
        assert not at.exception, f"Unhandled exception in {Path(page_file).name}: {at.exception}"
        assert len(at.error) == 0, (
            f"Unexpected error banner on {Path(page_file).name}: {[e.value for e in at.error]}"
        )
        assert len(at.markdown) >= 3
    finally:
        set_default_transport(None)


def test_interactive_filters_simulations_and_validation_errors(
    populated_frontend_transport: SyncFastAPITransport,
) -> None:
    """Verify interactive warehouse filtering, missing-record search, replenishment simulation, and 400 validation errors."""
    set_default_transport(populated_frontend_transport)
    try:
        # 1. Global sidebar warehouse scope filter on 01_Overview.py + LangGraph query
        at_ov = AppTest.from_file(str(FRONTEND_DIR / "pages" / "01_Overview.py"), default_timeout=60)
        at_ov.run()
        assert not at_ov.exception
        at_ov.sidebar.selectbox(key="sidebar_warehouse_select").select("WH-001").run()
        assert not at_ov.exception
        assert len(at_ov.error) == 0

        # 2. Missing records / unmatched search filter on 03_Inventory.py
        at_inv = AppTest.from_file(str(FRONTEND_DIR / "pages" / "03_Inventory.py"), default_timeout=60)
        at_inv.run()
        assert not at_inv.exception
        at_inv.text_input(key="inv_search").set_value("NON_EXISTENT_SKU_XYZ_999").run()
        assert not at_inv.exception
        assert any("No SKUs matched" in info.value for info in at_inv.info)

        # 3. Slotting Optimizer run execution (PROPOSED) + approval governance on 04_Slotting_Optimizer.py
        at_opt = AppTest.from_file(str(FRONTEND_DIR / "pages" / "04_Slotting_Optimizer.py"), default_timeout=60)
        at_opt.run()
        assert not at_opt.exception
        at_opt.selectbox(key="opt_strategy").select("greedy")
        at_opt.text_input(key="opt_sku_subset").set_value("SKU-000001, SKU-000209")
        at_opt.button(key="btn_exec_opt").click().run()
        assert not at_opt.exception
        # Partial run with unassigned oversized SKU-000209 surfaces warning banner cleanly
        assert any("PARTIAL" in w.value for w in at_opt.warning)

        # 4. Counterfactual Replenishment What-If simulation on 06_Replenishment.py
        at_rep = AppTest.from_file(str(FRONTEND_DIR / "pages" / "06_Replenishment.py"), default_timeout=60)
        at_rep.run()
        assert not at_rep.exception
        at_rep.button(key="btn_run_rep_sim").click().run()
        assert not at_rep.exception
        assert len(at_rep.error) == 0

        # 5. Exception state-machine validation error (RESOLVED -> DISMISSED -> 400 DOMAIN_VALIDATION_ERROR) on 07_Exceptions.py
        at_exc = AppTest.from_file(str(FRONTEND_DIR / "pages" / "07_Exceptions.py"), default_timeout=60)
        at_exc.run()
        assert not at_exc.exception
        at_exc.selectbox(key="exc_res_filter").select("RESOLVED").run()
        assert not at_exc.exception
        at_exc.selectbox(key="exc_target_status").select("DISMISSED")
        at_exc.button(key="btn_apply_exc_transition").click().run()
        assert not at_exc.exception
        assert any("DOMAIN_VALIDATION_ERROR" in err.value for err in at_exc.error)

        # 6. 18-rule Dataset Validator execution on 09_System_Health.py
        at_sys = AppTest.from_file(str(FRONTEND_DIR / "pages" / "09_System_Health.py"), default_timeout=60)
        at_sys.run()
        assert not at_sys.exception
        at_sys.button(key="btn_sys_validate").click().run()
        assert not at_sys.exception
        assert any("passed all 18 validation checks" in s.value for s in at_sys.success)
    finally:
        set_default_transport(None)


@pytest.mark.parametrize(
    "page_file",
    [
        str(FRONTEND_DIR / "app.py"),
        str(FRONTEND_DIR / "pages" / "01_Overview.py"),
        str(FRONTEND_DIR / "pages" / "04_Slotting_Optimizer.py"),
        str(FRONTEND_DIR / "pages" / "09_System_Health.py"),
    ],
)
def test_frontend_gracefully_handles_api_unavailable(page_file: str) -> None:
    """Verify offline FastAPI backend triggers a clean diagnostic banner without unhandled crashes."""
    set_default_transport(OfflineTransport())
    try:
        at = AppTest.from_file(page_file, default_timeout=30)
        at.run()
        assert not at.exception, f"Page crashed when API was offline: {at.exception}"
        assert any("FastAPI Backend Unavailable" in e.value for e in at.error)
    finally:
        set_default_transport(None)


@pytest.mark.parametrize(
    "page_file",
    [
        str(FRONTEND_DIR / "app.py"),
        str(FRONTEND_DIR / "pages" / "01_Overview.py"),
        str(FRONTEND_DIR / "pages" / "02_Warehouse_Explorer.py"),
        str(FRONTEND_DIR / "pages" / "03_Inventory.py"),
        str(FRONTEND_DIR / "pages" / "04_Slotting_Optimizer.py"),
        str(FRONTEND_DIR / "pages" / "05_Picking_Analytics.py"),
        str(FRONTEND_DIR / "pages" / "06_Replenishment.py"),
        str(FRONTEND_DIR / "pages" / "07_Exceptions.py"),
        str(FRONTEND_DIR / "pages" / "08_Scenario_Comparison.py"),
    ],
)
def test_frontend_gracefully_handles_empty_dataset(
    page_file: str,
    empty_frontend_transport: SyncFastAPITransport,
) -> None:
    """Verify empty operational database renders the Empty Operational Dataset warning without crashing."""
    set_default_transport(empty_frontend_transport)
    try:
        at = AppTest.from_file(page_file, default_timeout=45)
        at.run()
        assert not at.exception, f"Page {Path(page_file).name} crashed on empty dataset: {at.exception}"
        assert any("Empty Operational Dataset" in w.value for w in at.warning), (
            f"Expected Empty Operational Dataset banner on {Path(page_file).name}"
        )
    finally:
        set_default_transport(None)
