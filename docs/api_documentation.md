# SlotWise AI — REST API Documentation (Phase 5)

## 1. Architecture & Layer Separation

The SlotWise AI backend exposes a versioned REST API built with **FastAPI**, **Pydantic v2**, and **SQLAlchemy 2.x**.
All routes strictly avoid duplicating business rules or optimization formulas; every endpoint delegates to the exact domain services and repositories used by the CLI simulation pipelines and interactive dashboard:

```text
HTTP Request
   │
   ▼
FastAPI Router (backend/app/api/v1/*)
   ├── Pydantic v2 Request / Query Validation (backend/app/schemas/*)
   ├── Optional Write Authorization & Actor Attribution (require_write_access)
   ├── Atomic Session Scope (get_read_db / get_write_db)
   │
   ├──► Read Queries ──► SQLAlchemy 2.x Repositories (backend/app/repositories/*)
   └──► Mutations & Analytics ──► Domain Services:
          • DataPipelineService (generate, 18-rule validate, SHA-256 manifest)
          • CandidateGeneratorService & MultiFactorScorer
          • SlottingOptimizerService (OR-Tools CP-SAT + Greedy/Local-Swap)
          • PickingSimulationService (Nearest-Neighbor + 2-Opt aisle routing)
          • ReplenishmentSimulationService (ROP triggers + labor budget)
          • ExceptionDetectionService (operational rules + lifecycle state machine)
          • ScenarioComparisonService & AnalyticsKPIService
          • DecisionAssistantService (6-node grounded LangGraph workflow)
```

---

## 2. Server Startup & Interactive OpenAPI Docs

Start the API locally from the project root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

- **Liveness & DB Probe**: `http://127.0.0.1:8000/health`
- **Swagger UI**: `http://127.0.0.1:8000/docs`
- **ReDoc**: `http://127.0.0.1:8000/redoc`
- **OpenAPI 3.1 Schema**: `http://127.0.0.1:8000/openapi.json`

---

## 3. Authentication, Write Safeguards & Audit Attribution

- **Zero-Configuration Local Mode (Default)**: When `SLOTWISE_API_WRITE_TOKEN` is unset/empty, all endpoints work out of the box without external credentials.
- **Production Write Protection**: When `SLOTWISE_API_WRITE_TOKEN` is configured, all state-changing endpoints (`POST`, `PATCH`) enforce constant-time token verification (`secrets.compare_digest`) via either:
  - `X-API-Key: <token>`
  - `Authorization: Bearer <token>`
  Missing tokens return `401 Unauthorized`; invalid tokens return `403 Forbidden`.
- **Audit Actor Attribution**: Clients may pass `X-Actor: <operator_id>` (defaults to `API_OPERATOR`), which is recorded in `audit_events` for every optimization run, approval, rejection, exception transition, scenario creation, and persisted simulation.

---

## 4. Standardized Error Contract & Pagination

### Error Payload (`ErrorResponse`)
Internal database tracebacks and raw SQLAlchemy exceptions are never leaked to clients. Every non-2xx response conforms to:

```json
{
  "error_code": "ENTITY_NOT_FOUND",
  "message": "Warehouse 'WH-999' was not found.",
  "details": {
    "entity_type": "Warehouse",
    "entity_id": "WH-999"
  }
}
```

| HTTP Status | `error_code` | Cause |
| --- | --- | --- |
| `400` | `DOMAIN_VALIDATION_ERROR` | Illegal lifecycle state transition or violated domain rule |
| `401` | `HTTP_401` | Missing write token when `SLOTWISE_API_WRITE_TOKEN` is active |
| `403` | `HTTP_403` | Invalid write token |
| `404` | `ENTITY_NOT_FOUND` | Requested Warehouse, Location, SKU, Run, Scenario, or Exception ID does not exist |
| `409` | `CONFLICT_ERROR` | Conflicting resource mutation |
| `422` | `REQUEST_VALIDATION_ERROR` | Request JSON body or query parameter failed Pydantic v2 validation |
| `500` | `DATABASE_OPERATION_ERROR` / `INTERNAL_SERVER_ERROR` | Sanitized server/database failure |

### Paginated Envelope (`PaginatedResponse[T]`)
All collection endpoints accept `page` (`>= 1`) and `page_size` and return:

```json
{
  "items": [ "..." ],
  "meta": {
    "page": 1,
    "page_size": 25,
    "total_items": 250,
    "total_pages": 10
  }
}
```

---

## 5. Complete Endpoint Catalog

| Group | Method | Path | Summary |
| --- | --- | --- | --- |
| **Health** | `GET` | `/health` | Lightweight service & SQL connectivity check |
| **System & Audit** | `GET` | `/api/v1/system/status` | 21-table row counts, active profile, manifest version, ML & LangGraph status |
| **System & Audit** | `GET` | `/api/v1/system/audit-events` | Paginated immutable `AuditEvent` history |
| **Warehouses** | `GET` | `/api/v1/warehouses` | List facilities with aggregated slot, weight, and volume utilization |
| **Warehouses** | `GET` | `/api/v1/warehouses/{warehouse_id}` | Warehouse detail with child zones, permitted hazards, and aisles |
| **Locations** | `GET` | `/api/v1/locations` | Multi-filterable storage locations (`warehouse_id`, `zone_type`, `temperature_type`, `is_blocked`, `occupied_only`, sorting) |
| **Locations** | `GET` | `/api/v1/locations/{location_id}` | Location detail with remaining capacity, zone hazards, and active/historical assignments |
| **SKUs** | `GET` | `/api/v1/skus` | Filterable & sortable SKUs joined with active `SKUProfile` and active location |
| **SKUs** | `GET` | `/api/v1/skus/{sku_id}` | Full SKU detail with supplier, profile, daily demand trend, and assignment lineage |
| **SKUs** | `GET` | `/api/v1/skus/{sku_id}/profile` | Active analytical `SKUProfile` (velocity, ABC/XYZ, safety stock, ROP) |
| **SKUs** | `GET` | `/api/v1/skus/{sku_id}/candidates` | Ranked feasible candidate slots (6-factor breakdown) + rejected samples with explicit rejection codes |
| **Data Pipeline** | `POST` | `/api/v1/data/generate` | Generate deterministic synthetic dataset (`small`, `medium`, `full`) and optionally reload DB |
| **Data Pipeline** | `POST` | `/api/v1/data/validate` | Execute 18-check `DatasetValidator` against generated profile |
| **Data Pipeline** | `GET` | `/api/v1/data/manifest` | Retrieve SHA-256 reproducibility manifest |
| **Optimization** | `POST` | `/api/v1/optimization/run` | Execute constrained slotting optimization (`auto`, `ortools_cpsat`, `greedy`) |
| **Optimization** | `GET` | `/api/v1/optimization/runs` | Paginated list of immutable `OptimizationRun` records |
| **Optimization** | `GET` | `/api/v1/optimization/runs/{run_id}` | Single optimization run metadata and KPI summary |
| **Optimization** | `GET` | `/api/v1/optimization/runs/{run_id}/assignments` | Paginated assignments for a run (`include_reasons=true` attaches `AssignmentReason` rationales) |
| **Optimization** | `POST` | `/api/v1/optimization/runs/{run_id}/approve` | Transition `PROPOSED -> ACTIVE` while superseding prior `ACTIVE` records non-destructively |
| **Optimization** | `POST` | `/api/v1/optimization/runs/{run_id}/reject` | Transition `PROPOSED -> REJECTED` while preserving current `ACTIVE` placements |
| **Optimization** | `GET` | `/api/v1/optimization/assignments/history` | Full non-destructive assignment lineage across `ACTIVE`, `SUPERSEDED`, `PROPOSED`, and `REJECTED` |
| **Picking** | `POST` | `/api/v1/picking/simulate` | Simulate baseline vs. optimized slotting & Nearest-Neighbor+2-Opt routing on identical orders |
| **Picking** | `GET` | `/api/v1/picking/analytics` | Observed `PickEvent` telemetry + simulated tour comparison + recent orders |
| **Replenishment** | `POST` | `/api/v1/replenishment/simulate` | Simulate ROP replenishment tasks, labor budget allocation, and overload detection |
| **Replenishment** | `GET` | `/api/v1/replenishment/analytics` | Persisted replenishment queue analytics and per-warehouse labor utilization |
| **Exceptions** | `GET` | `/api/v1/exceptions` | Filterable exception list (`category`, `issue_type`, `severity`, `resolution_status`, optional read-only `include_ground_truth_eval`) |
| **Exceptions** | `POST` | `/api/v1/exceptions/scan` | Trigger operational exception scan (zero ground-truth access) |
| **Exceptions** | `GET` | `/api/v1/exceptions/{exception_id}` | Single exception detail |
| **Exceptions** | `PATCH` | `/api/v1/exceptions/{exception_id}/resolve` | State-machine validated lifecycle update (`OPEN -> IN_REVIEW -> RESOLVED / DISMISSED`) |
| **Analytics** | `GET` | `/api/v1/analytics/overview` | Executive KPI summary + isolated Ground-Truth Precision/Recall/F1 evaluation |
| **Analytics** | `GET` | `/api/v1/analytics/warehouse/{warehouse_id}` | Facility KPIs, zone capacity breakdown, and blocked-aisle summary |
| **Analytics** | `GET` | `/api/v1/analytics/optimization` | Optimization run history, 6-factor score component averages, and F1 benchmark |
| **Scenarios** | `POST` | `/api/v1/scenarios` | Create & persist a Baseline vs. `OptimizationRun` comparison scenario (`201 Created`) |
| **Scenarios** | `GET` | `/api/v1/scenarios` | List persisted comparison scenarios |
| **Scenarios** | `GET` | `/api/v1/scenarios/{scenario_id}/comparison` | Retrieve full baseline, optimized, and delta metrics for a scenario |
| **ML Forecasting** | `GET` | `/api/v1/ml/status` | ML demand forecaster readiness, `.joblib` artifact path, MLflow URI, and latest Naive vs. ML leaderboard |
| **ML Forecasting** | `POST` | `/api/v1/ml/train` | Train & evaluate chronological SKU demand forecasters, track runs in MLflow, and save forecast artifacts |
| **ML Forecasting** | `GET` | `/api/v1/ml/forecasts` | Paginated SKU-level 14-day demand forecasts, surge ratios, velocity shifts, and slotting/ROP recommendations |
| **Assistant** | `POST` | `/api/v1/assistant/query` | Ask natural-language operational questions to the grounded 6-node LangGraph Decision Assistant |

---

## 6. Verified Live Example Requests & Actual Responses

Captured via [`scripts/verify_live_api.py`](file:///e:/Projects/YottaFlexAI/scripts/verify_live_api.py) against a live Uvicorn instance (`artifacts/reports/phase5_live_api_verification.json`):

### A. Liveness & Connectivity Probe (`GET /health`)
```json
{
  "status": "HEALTHY",
  "service": "SlotWise AI",
  "version": "1.0.0",
  "environment": "local",
  "database_connected": true,
  "timestamp": "2026-10-31T05:13:56.131351"
}
```

### B. Explainable Candidate Slots & Hard-Constraint Rejections (`GET /api/v1/skus/SKU-000001/candidates?top_n=2&max_rejected_samples=2`)
```json
{
  "sku_id": "SKU-000001",
  "warehouse_scope": "ALL",
  "zone_scope": null,
  "total_locations_evaluated": 500,
  "total_feasible_count": 176,
  "total_rejected_count": 324,
  "rejection_reason_counts": {
    "AISLE_BLOCKED": 17,
    "DEDICATED_LOCATION_ALREADY_OCCUPIED": 185,
    "HAZARD_CONFLICT:LITHIUM_BATTERY_WITH_FLAMMABLE": 2,
    "HAZARD_NOT_PERMITTED:LITHIUM_BATTERY": 211,
    "TEMPERATURE_MISMATCH:AMBIENT_VS_CHILLED": 57,
    "TEMPERATURE_MISMATCH:AMBIENT_VS_CONTROLLED_ROOM": 42,
    "TEMPERATURE_MISMATCH:AMBIENT_VS_FROZEN": 42
  },
  "top_feasible": [
    {
      "sku_id": "SKU-000001",
      "location_id": "LOC-Z007-000020",
      "warehouse_id": "WH-001",
      "zone_id": "Z007",
      "zone_type": "HAZMAT",
      "accessibility_type": "FLOOR_PALLET",
      "is_feasible": true,
      "assigned_quantity": 5,
      "allocated_weight_kg": 19.84,
      "allocated_volume_m3": 0.046435,
      "total_score": 0.8257,
      "rank_position": 1
    }
  ]
}
```

### C. Picking Route & Slotting Simulation (`POST /api/v1/picking/simulate`)
**Request:**
```json
{
  "warehouse_id": "WH-001",
  "max_orders": 150
}
```
**Response (excerpt):**
```json
{
  "warehouse_scope": "WH-001",
  "orders_evaluated": 150,
  "order_lines_evaluated": 442,
  "blocked_stops_excluded": 0,
  "baseline_distance_m": 20737.8,
  "optimized_distance_m": 14974.0,
  "distance_saved_m": 5763.8,
  "percentage_improvement": 27.79,
  "pure_slotting_distance_saved_m": 3897.4,
  "baseline_time_seconds": 24339.29,
  "optimized_time_seconds": 19391.96,
  "estimated_time_saved_seconds": 4947.33,
  "time_improvement_pct": 20.33
}
```

### D. Grounded LangGraph Warehouse Decision Assistant (`POST /api/v1/assistant/query`)
**Request:**
```json
{
  "question": "Which SKUs have surging ML demand forecasts and what is our forecast WAPE?",
  "warehouse_id": "WH-001"
}
```
**Response (excerpt):**
```json
{
  "classified_intent": "DEMAND_FORECAST_AND_SURGE_ANALYSIS",
  "execution_mode": "DETERMINISTIC_GROUNDED_FALLBACK",
  "answer": "Based on chronological ML demand forecasting artifacts (DEMAND_FORECAST_AND_SURGE_ANALYSIS): Champion model RIDGE_REGRESSION achieved 137.79% daily WAPE (MAE=0.8947, +4.96% improvement vs. Naive 7d MA).",
  "recommended_actions": [
    "Run Slotting Optimization with `use_ml_forecasts=true` to promote surging SKUs toward dock-proximate fast-pick bays.",
    "Run Replenishment Simulation with `use_ml_forecasts=true` to evaluate proactive reorder-point triggers ahead of demand surges."
  ],
  "cited_entity_ids": ["WH-001", "SKU-000018"],
  "source_references": [
    {
      "entity_type": "WAREHOUSE",
      "entity_id": "WH-001",
      "source_table": "warehouses",
      "summary": "Apex North Distribution Center (ACTIVE)"
    }
  ],
  "grounding_verified": true
}
```

### E. Chronological SKU Demand Forecasting & MLflow (`POST /api/v1/ml/train` & `GET /api/v1/ml/forecasts`)
**Request (`POST /api/v1/ml/train`):**
```json
{
  "profile": "small",
  "forecast_horizon_days": 14,
  "train_ratio": 0.80
}
```
**Response (excerpt):**
```json
{
  "status": "COMPLETED",
  "profile": "small",
  "champion_model_name": "RIDGE_REGRESSION",
  "baseline_reference_model": "NAIVE_MOVING_AVG_7D",
  "wape_improvement_vs_naive_pct": 4.96,
  "mae_improvement_vs_naive_pct": 4.96,
  "mlflow_experiment_name": "SlotWise_SKU_Demand_Forecasting",
  "surging_sku_count": 7,
  "velocity_shift_sku_count": 48
}
```
