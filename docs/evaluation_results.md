# SlotWise AI — Verified Evaluation Results (Phases 1–4)

All metrics below are computed directly from reproducible operational executions (`seed = 20261022`) without manual adjustment or hardcoded constants. Machine-readable reports are persisted under [`artifacts/reports/`](file:///e:/Projects/YottaFlexAI/artifacts/reports/small_phase3_4_simulation_report.json).

---

## 1. Synthetic Dataset Validation & Dual-Run Reproducibility

| Dataset Profile | Warehouses | Locations | SKUs | Order Lines | Injected Ground-Truth Anomalies | 18-Rule Validator Status | Dual-Run SHA-256 Reproducibility |
|---|---:|---:|---:|---:|---:|---|---|
| `small` | 2 | 500 | 250 | 5,000 | 21 | **PASS (`18/18` checks, `0` unexpected errors)** | **PASS (`20/20` files byte-identical)** |
| `medium` | 5 | 2,500 | 1,000 | 25,000 | 51 | **PASS (`18/18` checks, `0` unexpected errors)** | Deterministic (`seed=20261022`) |
| `full` | 20 | 10,000 | 5,000 | 100,000 | 127 | **PASS (`18/18` checks, `0` unexpected errors)** | Deterministic (`seed=20261022`) |

---

## 2. Exception Detection vs. Isolated Ground Truth (`small` Profile)

[`ExceptionDetectionService`](file:///e:/Projects/YottaFlexAI/backend/app/services/exception_engine.py) scans strictly operational SQLite/PostgreSQL tables without reading any files under `data/ground_truth/`. Read-only post-hoc benchmarking via [`AnalyticsKPIService.evaluate_exception_detection_against_ground_truth`](file:///e:/Projects/YottaFlexAI/backend/app/analytics/kpi_service.py#L240-L300):

| Anomaly Category (`expected_issue_type`) | Expected (`ground_truth.json`) | Detected (`ExceptionRecord`) | True Positives | False Positives | False Negatives |
|---|---:|---:|---:|---:|---:|
| `CAPACITY_EXCEEDED` | 4 | 4 | 4 | 0 | 0 |
| `INCOMPATIBLE_COLOCATION` | 3 | 3 | 3 | 0 | 0 |
| `DUPLICATE_ACTIVE_ASSIGNMENT` | 3 | 3 | 3 | 0 | 0 |
| `BLOCKED_AISLE_PICK_PATH` | 4 | 4 | 4 | 0 | 0 |
| `REPLENISHMENT_LABOR_OVERLOAD` | 1 | 1 | 1 | 0 | 0 |
| `NO_COMPATIBLE_LOCATION` | 2 | 2 | 2 | 0 | 0 |
| `CAPACITY_NEARLY_EXHAUSTED` | 4 | 4 | 4 | 0 | 0 |
| **Total** | **21** | **21** | **21** | **0** | **0** |

- **Precision**: `1.0000`
- **Recall**: `1.0000`
- **F1 Score**: `1.0000`

---

## 3. End-to-End Constrained Slotting Optimization & Simulation (`small` Profile)

Executed via [`scripts/run_simulation.py`](file:///e:/Projects/YottaFlexAI/scripts/run_simulation.py) (`OptimizationRun` `OPT-2026-000001`, solver `ORTOOLS_CPSAT`, Scenario `SCN-2026-00001`):

| Metric | Baseline Layout | Optimized Layout (`OPT-2026-000001`) | Delta / Outcome |
|---|---:|---:|---:|
| Active Assigned SKUs | 242 unique (248 rows w/ duplicates) | **248** (`100%` of assignable SKUs) | All assignable SKUs placed; 2 physical `325x275x255 cm` outliers flagged `NO_COMPATIBLE_LOCATION` |
| Hard-Constraint Violations | 10 (`4` capacity, `3` incompatible, `3` duplicate) | **0** | **`-10` (`100%` eliminated)** |
| Near-Exhaustion Warnings ($\ge 92\%$) | 4 | **0** | **`-4` (`100%` eliminated)** |
| Average Multi-Factor Assignment Score | `0.6461` | **`0.7451`** | **`+0.0990` (`+15.3%` quality lift)** |
| Historical Assignment Preservation | 248 `ACTIVE` | 248 `SUPERSEDED` + 248 `ACTIVE` | **100% non-destructive audit trail (`244` SKUs physically relocated)** |
| Total Pick Travel Distance (`497` identical orders, `1,415` lines) | `67,608.4 m` | **`51,893.2 m`** | **`-15,715.2 m` (`23.24%` reduction; `9,765.0 m` from pure slotting)** |
| Total Fulfillment Pick Time (`497` identical orders) | `79,335.96 s` | **`65,268.08 s`** | **`-14,067.88 s` (`17.73%` time saved)** |
| Persisted Candidate Audit Records (`AssignmentReason`) | 0 | **2,098** (`750` rejected w/ reasons) | Full explainability across selected, alternative, and rejected slots |
