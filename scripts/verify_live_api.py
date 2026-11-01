"""Start a live Uvicorn FastAPI server, verify /health, /openapi.json, /docs, and key /api/v1/* endpoints over TCP HTTP, and save captured responses."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = PROJECT_ROOT / "artifacts" / "reports" / "phase5_live_api_verification.json"


def wait_for_server(base_url: str, timeout_seconds: float = 20.0) -> dict[str, Any]:
    """Wait until the live Uvicorn HTTP server responds 200 on /health."""
    deadline = time.perf_counter() + timeout_seconds
    last_err: Exception | None = None
    with httpx.Client(base_url=base_url, timeout=5.0) as client:
        while time.perf_counter() < deadline:
            try:
                resp = client.get("/health")
                if resp.status_code == 200:
                    return resp.json()
            except Exception as exc:
                last_err = exc
            time.sleep(0.3)
    raise RuntimeError(f"Uvicorn server at {base_url} did not become ready within {timeout_seconds}s: {last_err}")


def main() -> int:
    host = "127.0.0.1"
    port = 8015
    base_url = f"http://{host}:{port}"

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "backend.app.main:app",
        "--host",
        host,
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    captured: dict[str, Any] = {"server_url": base_url, "probes": {}}
    try:
        health_json = wait_for_server(base_url=base_url)
        captured["probes"]["GET /health"] = {"status_code": 200, "response": health_json}

        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            # 1. OpenAPI & Swagger UI
            oa_resp = client.get("/openapi.json")
            oa_resp.raise_for_status()
            oa_data = oa_resp.json()
            captured["probes"]["GET /openapi.json"] = {
                "status_code": oa_resp.status_code,
                "openapi_version": oa_data.get("openapi"),
                "title": oa_data.get("info", {}).get("title"),
                "version": oa_data.get("info", {}).get("version"),
                "path_count": len(oa_data.get("paths", {})),
                "paths": sorted(oa_data.get("paths", {}).keys()),
            }

            docs_resp = client.get("/docs")
            docs_resp.raise_for_status()
            captured["probes"]["GET /docs"] = {
                "status_code": docs_resp.status_code,
                "contains_swagger_ui": "Swagger UI" in docs_resp.text,
            }

            # 2. System status
            sys_resp = client.get("/api/v1/system/status")
            sys_resp.raise_for_status()
            captured["probes"]["GET /api/v1/system/status"] = {
                "status_code": sys_resp.status_code,
                "response": sys_resp.json(),
            }

            # 3. Warehouse detail
            wh_resp = client.get("/api/v1/warehouses/WH-001")
            wh_resp.raise_for_status()
            wh_json = wh_resp.json()
            captured["probes"]["GET /api/v1/warehouses/WH-001"] = {
                "status_code": wh_resp.status_code,
                "response_summary": {
                    "warehouse_id": wh_json["warehouse_id"],
                    "warehouse_name": wh_json["warehouse_name"],
                    "total_locations": wh_json["total_locations"],
                    "occupied_locations": wh_json["occupied_locations"],
                    "location_utilization_pct": wh_json["location_utilization_pct"],
                    "zone_count": wh_json["zone_count"],
                    "aisle_count": wh_json["aisle_count"],
                    "blocked_aisle_count": wh_json["blocked_aisle_count"],
                },
            }

            # 4. SKU candidate generation & rejection reasons
            cand_resp = client.get(
                "/api/v1/skus/SKU-000001/candidates",
                params={"top_n": 2, "max_rejected_samples": 2},
            )
            cand_resp.raise_for_status()
            captured["probes"]["GET /api/v1/skus/SKU-000001/candidates?top_n=2&max_rejected_samples=2"] = {
                "status_code": cand_resp.status_code,
                "response": cand_resp.json(),
            }

            # 5. Optimization runs
            runs_resp = client.get("/api/v1/optimization/runs")
            runs_resp.raise_for_status()
            captured["probes"]["GET /api/v1/optimization/runs"] = {
                "status_code": runs_resp.status_code,
                "response": runs_resp.json(),
            }

            # 6. Picking simulation
            pick_req = {"warehouse_id": "WH-001", "max_orders": 150}
            pick_resp = client.post("/api/v1/picking/simulate", json=pick_req)
            pick_resp.raise_for_status()
            p_json = pick_resp.json()
            p_summary = {k: v for k, v in p_json.items() if k != "sample_orders"}
            p_summary["sample_order_0"] = p_json["sample_orders"][0] if p_json["sample_orders"] else None
            captured["probes"]["POST /api/v1/picking/simulate"] = {
                "request": pick_req,
                "status_code": pick_resp.status_code,
                "response": p_summary,
            }

            # 7. Open operational exceptions
            exc_resp = client.get("/api/v1/exceptions", params={"resolution_status": "OPEN", "page_size": 3})
            exc_resp.raise_for_status()
            captured["probes"]["GET /api/v1/exceptions?resolution_status=OPEN&page_size=3"] = {
                "status_code": exc_resp.status_code,
                "response": exc_resp.json(),
            }

            # 8. Executive analytics overview
            ana_resp = client.get("/api/v1/analytics/overview")
            ana_resp.raise_for_status()
            captured["probes"]["GET /api/v1/analytics/overview"] = {
                "status_code": ana_resp.status_code,
                "response": ana_resp.json(),
            }

            # 9. Scenario comparison
            scn_resp = client.get("/api/v1/scenarios")
            scn_resp.raise_for_status()
            captured["probes"]["GET /api/v1/scenarios"] = {
                "status_code": scn_resp.status_code,
                "response": scn_resp.json(),
            }

            # 10. Grounded LangGraph Decision Assistant
            asst_req = {
                "question": "What are the picking distance savings across orders in WH-001?",
                "warehouse_id": "WH-001",
            }
            asst_resp = client.post("/api/v1/assistant/query", json=asst_req)
            asst_resp.raise_for_status()
            captured["probes"]["POST /api/v1/assistant/query"] = {
                "request": asst_req,
                "status_code": asst_resp.status_code,
                "response": asst_resp.json(),
            }

        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(captured, indent=2), encoding="utf-8")
        print(json.dumps(captured, indent=2))
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
