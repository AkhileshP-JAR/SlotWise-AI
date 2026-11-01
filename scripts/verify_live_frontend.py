"""Live dual-server (FastAPI Uvicorn + Streamlit) end-to-end verification script for Phase 6."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BACKEND_PORT = 8016
FRONTEND_PORT = 8516
BACKEND_URL = f"http://127.0.0.1:{BACKEND_PORT}"
FRONTEND_URL = f"http://127.0.0.1:{FRONTEND_PORT}"

PAGES_TO_VERIFY: list[str] = [
    "frontend/app.py",
    "frontend/pages/01_Overview.py",
    "frontend/pages/02_Warehouse_Explorer.py",
    "frontend/pages/03_Inventory.py",
    "frontend/pages/04_Slotting_Optimizer.py",
    "frontend/pages/05_Picking_Analytics.py",
    "frontend/pages/06_Replenishment.py",
    "frontend/pages/07_Exceptions.py",
    "frontend/pages/08_Scenario_Comparison.py",
    "frontend/pages/09_System_Health.py",
]


def _wait_for_http(url: str, timeout_s: float = 30.0) -> httpx.Response:
    deadline = time.perf_counter() + timeout_s
    last_err: Exception | None = None
    while time.perf_counter() < deadline:
        try:
            resp = httpx.get(url, timeout=3.0)
            if resp.status_code == 200:
                return resp
        except Exception as exc:
            last_err = exc
        time.sleep(0.4)
    raise RuntimeError(f"Service at {url} did not become ready within {timeout_s}s (last_err={last_err})")


def main() -> int:
    env = os.environ.copy()
    env["SLOTWISE_BACKEND_URL"] = BACKEND_URL
    env["PYTHONPATH"] = str(PROJECT_ROOT)

    uvicorn_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "backend.app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(BACKEND_PORT),
        "--log-level",
        "warning",
    ]
    streamlit_cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(PROJECT_ROOT / "frontend" / "app.py"),
        "--server.address",
        "127.0.0.1",
        "--server.port",
        str(FRONTEND_PORT),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]

    backend_proc: subprocess.Popen[str] | None = None
    frontend_proc: subprocess.Popen[str] | None = None

    report: dict[str, Any] = {
        "backend_url": BACKEND_URL,
        "frontend_url": FRONTEND_URL,
        "live_server_probes": {},
        "pages_verified": {},
        "all_pages_passed": False,
    }

    try:
        print(f"[1/4] Starting live FastAPI backend at {BACKEND_URL} ...")
        backend_proc = subprocess.Popen(
            uvicorn_cmd,
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        be_health = _wait_for_http(f"{BACKEND_URL}/health", timeout_s=25.0)
        report["live_server_probes"]["fastapi_health"] = {
            "status_code": be_health.status_code,
            "payload": be_health.json(),
        }
        print(f"      FastAPI /health -> {be_health.status_code} ({be_health.json().get('status')})")

        print(f"[2/4] Starting live Streamlit server at {FRONTEND_URL} ...")
        frontend_proc = subprocess.Popen(
            streamlit_cmd,
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        st_health = _wait_for_http(f"{FRONTEND_URL}/_stcore/health", timeout_s=30.0)
        st_root = _wait_for_http(f"{FRONTEND_URL}/", timeout_s=15.0)
        report["live_server_probes"]["streamlit_health"] = {
            "status_code": st_health.status_code,
            "body": st_health.text.strip(),
        }
        report["live_server_probes"]["streamlit_root_html"] = {
            "status_code": st_root.status_code,
            "html_bytes": len(st_root.text),
            "contains_streamlit_shell": "Streamlit" in st_root.text
            or "stApp" in st_root.text
            or '<div id="root">' in st_root.text,
        }
        print(
            f"      Streamlit /_stcore/health -> {st_health.status_code} ({st_health.text.strip()}), "
            f"Root HTML -> {st_root.status_code} ({len(st_root.text)} bytes)"
        )

        print(f"[3/4] Executing all {len(PAGES_TO_VERIFY)} Streamlit pages against live FastAPI HTTP server ...")
        os.environ["SLOTWISE_BACKEND_URL"] = BACKEND_URL

        all_ok = True
        for rel_page in PAGES_TO_VERIFY:
            abs_page = PROJECT_ROOT / rel_page
            t0 = time.perf_counter()
            at = AppTest.from_file(str(abs_page), default_timeout=60)
            at.run()
            dt_ms = round((time.perf_counter() - t0) * 1000.0, 2)

            has_exc = bool(at.exception)
            errors = [e.value for e in at.error]
            warnings = [w.value for w in at.warning]
            page_ok = (not has_exc) and (len(errors) == 0)
            if not page_ok:
                all_ok = False

            report["pages_verified"][rel_page] = {
                "passed": page_ok,
                "has_exception": has_exc,
                "exceptions": [str(e.value) for e in at.exception] if has_exc else [],
                "error_banners": errors,
                "warning_banners": warnings,
                "execution_ms": dt_ms,
                "widget_summary": {
                    "markdown_blocks": len(at.markdown),
                    "dataframes": len(at.dataframe),
                    "selectboxes": len(at.selectbox),
                    "buttons": len(at.button),
                    "tabs": len(at.tabs),
                },
            }
            status_tag = "PASS" if page_ok else "FAIL"
            print(
                f"      [{status_tag}] {rel_page:<42} | {dt_ms:>7.1f} ms | "
                f"md={len(at.markdown)} df={len(at.dataframe)} sel={len(at.selectbox)} btn={len(at.button)}"
            )

        report["all_pages_passed"] = all_ok

        out_path = PROJECT_ROOT / "artifacts" / "reports" / "phase6_frontend_verification.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"[4/4] Saved verification report -> {out_path}")

        return 0 if all_ok else 1

    finally:
        for proc in (frontend_proc, backend_proc):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=8.0)
                except subprocess.TimeoutExpired:
                    proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
