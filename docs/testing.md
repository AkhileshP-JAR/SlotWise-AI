# SlotWise AI — Data Validation, Reproducibility & Testing Guide

## 1. Automated Data Validation Framework (18 Checks)

Implemented in [`DatasetValidator`](file:///e:/Projects/YottaFlexAI/backend/app/validators/dataset_validator.py) and executable via [`scripts/validate_data.py`](file:///e:/Projects/YottaFlexAI/scripts/validate_data.py), validation evaluates generated or persisted operational datasets across 18 deterministic checks:

| # | Check Code | Scope & Verification Rule |
|---|---|---|
| 01 | `PK_UNIQUENESS` | Verifies zero null or duplicate primary keys across all 16 operational tables (including composite bridge PK `(zone_id, hazard_class_id)`). |
| 02 | `FK_INTEGRITY` | Verifies 26 parent-child foreign-key relationships for zero orphan references and strict non-nullability. |
| 03 | `ID_CONVENTION_FORMAT` | Verifies regex compliance (`WH-001`, `LOC-Z001-000001`, `SKU-000001`, `ORD-2026-000001`, `PICK-ORD-2026-000001-001`, `SLOT-2026-000001`, `REP-2026-000001`). |
| 04 | `ENUM_DOMAIN_VALIDITY` | Pandera schema verification of all categorical status, priority, hazard, temperature, ABC/XYZ, and velocity enums. |
| 05 | `REQUIRED_NON_NULL` | Pandera non-nullable enforcement across mandatory operational attributes. |
| 06 | `DIMENSION_POSITIVITY` | Strictly positive `width_cm`, `height_cm`, and `depth_cm` on `locations` and `skus`. |
| 07 | `WEIGHT_POSITIVITY` | Strictly positive `unit_weight_kg` and `max_weight_kg`; non-negative `current_weight_kg`. |
| 08 | `VOLUME_CONSISTENCY` | Reconciles `Location.max_volume_m3` against `(width_cm * height_cm * depth_cm) / 1,000,000`. |
| 09 | `CAPACITY_CONSTRAINTS` | Detects hard weight/volume overloads (`CAPACITY_EXCEEDED`) and $\ge 92\%$ near-exhaustion warnings (`CAPACITY_NEARLY_EXHAUSTED`). |
| 10 | `TEMPORAL_DATE_ORDERING` | Ensures `effective_from <= effective_to` and `order_date <= pick_timestamp`. |
| 11 | `LIFECYCLE_TRANSITIONS` | Verifies `ACTIVE` assignments carry `effective_to IS NULL` and follow legal lifecycle states. |
| 12 | `DUPLICATE_ACTIVE_ASSIGNMENTS` | Flags `DEDICATED_SINGLE_SKU` locations holding $>1$ `ACTIVE` slot assignment. |
| 13 | `WAREHOUSE_ZONE_CONSISTENCY` | Confirms `SlotAssignment.warehouse_id == Location.zone.warehouse_id`. |
| 14 | `SKU_PROFILE_CONSISTENCY` | Recomputes `ADV`, `ABC`, `XYZ`, and `ROP` from raw `order_lines` via `DemandAnalyticsService` to confirm zero drift. |
| 15 | `ORDER_LINE_ARITHMETIC` | Verifies `abs(line_value - round(quantity * unit_price, 2)) <= 0.01`. |
| 16 | `PICK_EVENT_RECONCILIATION` | Verifies `quantity_picked <= order_line.quantity` and flags blocked-aisle traversal (`BLOCKED_AISLE_PICK_PATH`). |
| 17 | `REPLENISHMENT_RECONCILIATION` | Aggregates active replenishment labor minutes per warehouse against `daily_labor_minutes_capacity` (`REPLENISHMENT_LABOR_OVERLOAD`). |
| 18 | `GROUND_TRUTH_SEPARATION` | Verifies zero forbidden ground-truth columns (`ground_truth`, `is_anomaly`, `anomaly_id`, `expected_issue_type`, etc.) exist in any operational table. |

### Classification of Findings
- **`ERROR`**: Unexpected structural, referential, arithmetic, leakage, or unlabelled rule corruption (`is_valid = False`).
- **`WARNING`**: Unlabelled operational threshold alerts.
- **`EXPECTED_LABELLED_ANOMALY`**: Intentional injected anomalies reconciled 1-to-1 against `data/ground_truth/<profile>/ground_truth.csv` during audit validation.

---

## 2. Reproducibility Protocol

Executed via [`scripts/reproduce_dataset.py`](file:///e:/Projects/YottaFlexAI/scripts/reproduce_dataset.py):
1. Runs `SyntheticDataGenerator` twice with fixed seed `20261022` into two isolated temporary directories (`run_a`, `run_b`).
2. Normalizes line endings (`\n`) and sorts records deterministically by primary key (`kind="mergesort"`).
3. Verifies byte-identical **SHA-256 checksums** across all 20 exported CSV, JSON, SQL, and ground-truth files, plus identical entity counts and 18-rule validation outcomes.

---

## 3. Business Rule, Optimization & Mandatory Negative Scenario Suite

Implemented in [`backend/tests/test_phase3_4_business_and_simulation.py`](file:///e:/Projects/YottaFlexAI/backend/tests/test_phase3_4_business_and_simulation.py), automated unit and integration tests verify:

- **Demand & Inventory Analytics**: Exact `ADV`, dense zero-day `StdDev`, Fast/Medium/Slow velocity percentiles, `ABC` Pareto revenue bands (`80% / 95%`), `XYZ` coefficient-of-variation bands (`0.50 / 1.00`), and `Safety Stock` + `Reorder Point` formulas.
- **Physical & Environmental Compatibility**: Cubic meter volume conversion, available weight/volume headroom, 9 hard-constraint checks, and explicit rejection codes (`DIM_*_EXCEEDED`, `WEIGHT_CAPACITY_INSUFFICIENT`, `TEMPERATURE_MISMATCH`, `HAZARD_NOT_PERMITTED`, `HAZARD_CONFLICT`, `CATEGORY_CONFLICT`, `DEDICATED_LOCATION_ALREADY_OCCUPIED`, `AISLE_BLOCKED`).
- **Explainable Multi-Factor Scoring & Ranking**: Normalized `[0.0, 1.0]` component scores, deterministic tie-breaking, and proof that infeasible candidates always receive `total_score = 0.0` regardless of soft weight settings.
- **All 5 Mandatory Negative Scenarios**:
  1. Oversized / overweight SKU exceeding location dimensions and remaining capacity.
  2. Incompatible hazard/food-chemical co-location and temperature/zone mismatches.
  3. Duplicate active assignment on a `DEDICATED_SINGLE_SKU` location.
  4. Pick path traversing or crossing a blocked aisle (`AISLE_BLOCKED` slotting rejection, route exclusion/detour, and `BLOCKED_AISLE_PICK_PATH` detection).
  5. Replenishment workload exceeding warehouse `daily_labor_minutes_capacity` (`OVERLOADED` task status and `REPLENISHMENT_LABOR_OVERLOAD` detection).
- **Constrained Optimization, History & Simulation**: OR-Tools CP-SAT and Deterministic Greedy + 2-Way Local Swap solvers, non-destructive `PROPOSED -> REJECTED` and `PROPOSED -> ACTIVE -> SUPERSEDED` lifecycle transitions, baseline vs. optimized picking comparison on identical orders, scenario evaluation, and Ground-Truth Precision/Recall/F1 benchmarking.

---

## 4. Phase 5 FastAPI REST Endpoint Integration Suite (`HTTPX`)

Implemented in [`backend/tests/test_phase5_api.py`](file:///e:/Projects/YottaFlexAI/backend/tests/test_phase5_api.py) using `httpx.AsyncClient` over `httpx.ASGITransport` against an isolated SQLite test database:

- **Health, OpenAPI & System Status**: Verifies `/health`, `/openapi.json`, `/docs`, and `/api/v1/system/status` (21-table record counts, manifest version, ML & LangGraph readiness).
- **Master-Data Filtering, Sorting & Pagination**: Tests `/api/v1/warehouses`, `/api/v1/locations`, `/api/v1/skus`, `/api/v1/skus/{sku_id}/profile`, and `/api/v1/skus/{sku_id}/candidates` (verifying ranked feasible slots + explainable rejection codes).
- **Exception Detection & State Machine**: Tests `POST /api/v1/exceptions/scan` (21/21 anomalies detected), filtered `GET /api/v1/exceptions`, valid `OPEN -> IN_REVIEW -> RESOLVED` transitions, and HTTP `400 DOMAIN_VALIDATION_ERROR` on illegal `RESOLVED -> DISMISSED` transitions.
- **Optimization Governance & Non-Destructive History**: Tests `POST /api/v1/optimization/run`, `POST /runs/{run_id}/reject`, `POST /runs/{run_id}/approve` (`248` approved, `248` superseded, `14` remediated exceptions auto-resolved), and `GET /assignments/history`.
- **Simulations, Scenarios, Analytics & Grounded LangGraph Assistant**: Tests `/api/v1/picking/*`, `/api/v1/replenishment/*`, `/api/v1/scenarios` (`201 Created`), `/api/v1/analytics/*` (`F1 = 1.0`), `/api/v1/assistant/query` (`grounding_verified = true`, 6-node trace), and `/api/v1/system/audit-events`.
- **Data Pipeline, Validation Errors & Write Authorization**: Tests `/api/v1/data/manifest`, `/api/v1/data/validate`, `/api/v1/data/generate`, `404`/`422` structured `ErrorResponse` payloads, and `401`/`403`/`200` `X-API-Key` & `Bearer` write-token enforcement.

---

## 5. Phase 6 Streamlit Frontend & Dual-Server Integration Suite (`AppTest`)

Implemented in [`backend/tests/test_phase6_frontend.py`](file:///e:/Projects/YottaFlexAI/backend/tests/test_phase6_frontend.py) and [`scripts/verify_live_frontend.py`](file:///e:/Projects/YottaFlexAI/scripts/verify_live_frontend.py):

- **All 10 Streamlit Entrypoints Verified**: Exercises [`frontend/app.py`](file:///e:/Projects/YottaFlexAI/frontend/app.py) and all nine operational pages (`01_Overview.py` .. `09_System_Health.py`) strictly through [`SlotWiseAPIClient`](file:///e:/Projects/YottaFlexAI/frontend/components/api_client.py) with zero independent frontend datasets or hardcoded KPIs.
- **Interactive Workflows & Validation Error Handling**:
  - Global sidebar warehouse scope filtering (`WH-001` / `ALL`).
  - SKU search & missing-record graceful empty filter states (`03_Inventory.py`).
  - Constrained slotting optimization execution surfacing `PARTIAL` run alerts and unassigned SKU hard-constraint diagnostics (`04_Slotting_Optimizer.py`).
  - Counterfactual shift-labor replenishment simulation (`06_Replenishment.py`).
  - Exception lifecycle state-machine validation surfacing structured `400 DOMAIN_VALIDATION_ERROR` banners on illegal `RESOLVED -> DISMISSED` transitions (`07_Exceptions.py`).
  - 18-rule dataset validator execution (`09_System_Health.py`).
- **UI Edge-State Resilience**:
  - **API Unavailable (`APIUnavailableError`)**: Injects an `OfflineTransport` raising `httpx.ConnectError` and verifies clean diagnostic banners with zero unhandled Python tracebacks.
  - **Empty Operational Dataset**: Connects to an initialized 21-table SQLite database with `0` rows and verifies clean `Empty Operational Dataset` guidance across pages.
- **Live Dual-Server Verification (`scripts/verify_live_frontend.py`)**: Starts live `uvicorn` and `streamlit run` subprocesses, probes FastAPI `/health` and Streamlit `/_stcore/health` + root HTML shell, executes every page over live TCP HTTP, and writes [`artifacts/reports/phase6_frontend_verification.json`](file:///e:/Projects/YottaFlexAI/artifacts/reports/phase6_frontend_verification.json).

---

## 6. Phase 7 ML Demand Forecasting & LangGraph Agentic AI Suite

Implemented in [`backend/tests/test_phase7_ml_and_agents.py`](file:///e:/Projects/YottaFlexAI/backend/tests/test_phase7_ml_and_agents.py) and [`scripts/train_forecaster.py`](file:///e:/Projects/YottaFlexAI/scripts/train_forecaster.py):

- **Exact Regression Metrics**: Verifies `compute_regression_metrics` for `MAE`, `RMSE`, and `WAPE (%)`, non-negative clipping, and zero-division protection.
- **Chronological Split & Zero Temporal Leakage**: Proves `train_end_date < test_start_date` (`chronological_order_verified = True`) and mutates post-cutoff future observations by `+50,000` units to prove pre-cutoff features and SKU priors remain bit-for-bit invariant.
- **Naive vs. ML Evaluation, MLflow & Artifacts**: Compares `NAIVE_LAST_OBSERVATION` and `NAIVE_MOVING_AVG_7D` against `RIDGE_REGRESSION` and `HIST_GRADIENT_BOOSTING_POISSON`, verifies local MLflow SQLite run logging (`mlflow.search_runs`), and validates `.joblib` + JSON forecast persistence.
- **Deterministic Core Preservation & Opt-In Forecast Integration**: Proves `use_ml_forecasts=False` leaves replenishment and slotting outputs 100% deterministic, while `use_ml_forecasts=True` activates forecast-guided ROPs and slotting scores with `constraint_violation_count = 0`.
- **LangGraph Decision Assistant (AI-Enabled, Fallback & Anti-Fabrication Firewall)**:
  - Verifies zero-key `DETERMINISTIC_GROUNDED_FALLBACK` (including graceful degradation when `llm_provider="groq"` has no API key).
  - Verifies AI-enabled `LLM_GROUNDED` structured Pydantic synthesis via LangChain `BaseChatModel` injection.
  - Verifies the relational anti-fabrication firewall (`validate_grounding`) intercepts hallucinated IDs (`SKU-999999`, `LOC-Z999-999999`, `EXC-2099-888888`) and reverts to `DETERMINISTIC_FALLBACK_AFTER_GROUNDING_GUARD`.

---

## 7. Running Commands

```powershell
# Generate, validate, load, and verify reproducibility (SMALL / MEDIUM / FULL)
.\.venv\Scripts\python.exe scripts/generate_data.py --profile small
.\.venv\Scripts\python.exe scripts/validate_data.py --profile small
.\.venv\Scripts\python.exe scripts/load_data.py --profile small
.\.venv\Scripts\python.exe scripts/reproduce_dataset.py --profile small

# Run end-to-end Phase 3 & 4 optimization, exception detection, and simulation
.\.venv\Scripts\python.exe scripts/run_simulation.py --profile small

# Train chronological SKU demand forecasters & log MLflow runs (Phase 7)
.\.venv\Scripts\python.exe scripts/train_forecaster.py --profile small --horizon 14 --train-ratio 0.80

# Start FastAPI REST Server (Terminal 1)
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000

# Start Streamlit Enterprise Frontend (Terminal 2)
.\.venv\Scripts\python.exe -m streamlit run frontend/app.py --server.port 8501

# Verify live FastAPI + Streamlit dual-server stack across all 9 pages
.\.venv\Scripts\python.exe scripts/verify_live_frontend.py

# Execute automated Pytest test suite (Phases 1–7)
.\.venv\Scripts\python.exe -m pytest -v
```
