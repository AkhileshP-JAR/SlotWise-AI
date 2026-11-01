# SlotWise AI — Phase 7 ML Demand Forecasting & Grounded LangGraph Decision Assistant

## 1. Design Philosophy: Enhancing Without Compromising the Deterministic Core

SlotWise AI enforces a strict architectural boundary between its **deterministic operations research core** and its **advanced ML / Agentic AI layer**:

1. **Zero Mandatory External Credentials**: Both the SKU-level ML demand forecaster and the LangGraph Warehouse Decision Assistant operate 100% locally out of the box without any external API keys, cloud tracking servers, or SaaS dependencies.
2. **Opt-In Forecast Guidance (`use_ml_forecasts: bool = False`)**: By default, `SlottingOptimizerService` and `ReplenishmentSimulationService` execute strictly over observed deterministic `SKUProfile` parameters. Operators or API clients can explicitly enable `use_ml_forecasts=True` to evaluate or apply forecast-guided slotting velocity percentiles and proactive reorder points while preserving all 10 hard physical, thermal, hazard, and capacity constraints (`constraint_violation_count = 0`).
3. **Zero Fabrication Guarantee**: The LangGraph Decision Assistant validates every structured output with **Pydantic v2** and runs a post-synthesis **Relational Grounding Firewall (`validate_grounding`)** against SQLite/PostgreSQL tables, automatically intercepting and blocking any fabricated SKU, Location, Warehouse, Optimization Run, Scenario, Replenishment, or Exception ID.

---

## 2. SKU-Level Chronological Demand Forecasting (`backend/app/ml/`)

Implemented in [`DemandForecastingService`](file:///e:/Projects/YottaFlexAI/backend/app/ml/forecaster.py) and executable via [`scripts/train_forecaster.py`](file:///e:/Projects/YottaFlexAI/scripts/train_forecaster.py) or `POST /api/v1/ml/train`.

### 2.1 Dense Daily Panel & Strictly Causal Feature Engineering
1. Builds a complete dense `(sku_id, order_date)` panel across all active SKUs over the contiguous calendar window (`250 SKUs × 90 days = 22,500 SKU-day rows` on `small`), filling zero-order days explicitly with `0.0`.
2. Computes **29 strictly causal features** (`FEATURE_COLUMNS`) using shifted series ($t-1, t-2, \dots$) so day $t$ never observes contemporaneous or future demand:
   - **Causal Autoregressive Lags**: `lag_1`, `lag_2`, `lag_3`, `lag_7`, `lag_14`.
   - **Causal Rolling & Exponential Statistics (computed over `lag_1`)**: `rolling_mean_7`, `rolling_std_7`, `rolling_mean_14`, `rolling_mean_28`, `rolling_max_14`, `nonzero_rate_14`, `ewma_7`, `momentum_7_28`.
   - **Train-Window-Only SKU Priors (`order_date <= cutoff_date`)**: `sku_train_mean`, `sku_train_std`, `sku_train_nonzero_rate`, `sku_train_dow_mean` (never touches holdout test rows).
   - **Calendar & Static SKU Metadata**: `day_of_week`, `is_weekend`, `day_of_month`, cyclical `sin_dow`/`cos_dow`, `day_index_norm`, `log_unit_price`, `unit_weight_kg`, `unit_volume_m3`, `is_fragile`, supplier `average_lead_time_days`, and `category_code`.

### 2.2 Chronological Train / Holdout Split
- **Warmup Window**: `2026-01-01 .. 2026-01-14` (`14 days` — initializes 14-day lags without boundary bias).
- **Training Window**: `2026-01-15 .. 2026-03-13` (`58 days`, `14,500` rows on `small`).
- **Holdout Test Window**: `2026-03-14 .. 2026-03-31` (`18 days`, `4,500` rows on `small`).
- Enforces `max(train_date) < min(test_date)` (`chronological_order_verified = True`) and is verified against synthetic post-cutoff `+50,000` demand mutations in `test_chronological_split_and_causal_features_zero_future_leakage`.

### 2.3 Naive Baselines vs. ML Regressors & Evaluation Metrics
Evaluates **MAE**, **RMSE**, and **WAPE (%)**:
$$\text{WAPE}(\%) = \frac{\sum_{i} |y_i - \hat{y}_i|}{\sum_{i} |y_i|} \times 100$$
across two complementary operational granularities:
1. **1-Step Daily SKU-Day Metrics (`daily_mae`, `daily_rmse`, `daily_wape_pct`)**: Daily demand accuracy across all holdout `(sku_id, date)` rows.
2. **Cutoff-Origin Multi-Step Cumulative Horizon Metrics (`horizon_mae`, `horizon_rmse`, `horizon_wape_pct`)**: Cumulative demand accuracy across the entire 18-day holdout horizon standing strictly at `cutoff_date = 2026-03-13` with zero peeking at intermediate holdout days.

#### Verified Holdout Benchmark (`small` Profile, Seed `20261022`)

| Model Name | Family | Daily MAE | Daily RMSE | Daily WAPE (%) | 18-Day Horizon WAPE (%) |
| --- | --- | ---: | ---: | ---: | ---: |
| `NAIVE_LAST_OBSERVATION` | `NAIVE_BASELINE` | `1.0302` | `3.2931` | `158.66%` | `194.66%` |
| `NAIVE_MOVING_AVG_7D` | `NAIVE_BASELINE` | `0.9414` | `2.4289` | `144.97%` | `94.92%` |
| **`RIDGE_REGRESSION` (Champion)** | `MACHINE_LEARNING` | **`0.8947`** | `2.3839` | **`137.79%`** | **`62.95%`** |
| `HIST_GRADIENT_BOOSTING_POISSON` | `MACHINE_LEARNING` | `0.9335` | **`2.3605`** | `143.77%` | **`57.33%`** |

### 2.4 MLflow Experiment Tracking & Artifact Persistence
- **Local MLflow SQLite Store**: Logs all 4 runs, hyperparameters, chronological split dates, metrics, and evaluation JSON artifacts to `sqlite:///artifacts/mlruns/mlflow.db` under experiment `SlotWise_SKU_Demand_Forecasting`.
- **Saved Model & Forecast Artifacts**:
  - `artifacts/models/demand_forecaster_<profile>.joblib` (and `_latest.joblib`): Serialized bundle containing champion estimator, feature schema, SKU priors, chronological split metadata, and MLflow run ID.
  - `artifacts/models/forecast_evaluation_<profile>.json`: Complete leaderboard and top surging SKUs.
  - `artifacts/models/sku_forecasts_<profile>.json`: 14-day multi-step daily trajectory, surge ratio, forecast velocity tier, forecast reorder point, `slotting_recommendation`, and `replenishment_recommendation` for every active SKU.

### 2.5 Operational Slotting & Replenishment Integration
- **Slotting Optimizer (`POST /api/v1/optimization/run` with `use_ml_forecasts=true`)**: Blends forecasted daily demand, velocity tier, and velocity percentile into `CandidateGeneratorService`, prioritizing surging SKUs for dock-proximate fast-pick slots and annotating `[ML Forecast ADV=..., surge=...x, tier=...]` in `SlotAssignment.decision_reason` while strictly enforcing all 10 hard constraints.
- **Replenishment Simulator (`POST /api/v1/replenishment/simulate` with `use_ml_forecasts=true`)**: Evaluates forward-pick triggers against `forecast_reorder_point` and `forecast_daily_demand` to surface proactive replenishment workloads ahead of demand surges.

---

## 3. Grounded LangGraph Warehouse Decision Assistant (`backend/app/agents/`)

Implemented in [`DecisionAssistantService`](file:///e:/Projects/YottaFlexAI/backend/app/agents/assistant_service.py) and [`LLMProviderAdapter`](file:///e:/Projects/YottaFlexAI/backend/app/agents/llm_adapter.py).

### 3.1 6-Node StateGraph Architecture

```mermaid
flowchart LR
    Q["User Question"] --> N1["1. classify_intent"]
    N1 --> N2["2. retrieve_warehouse_context"]
    N2 --> N3["3. retrieve_kpi_and_exceptions"]
    N3 --> N4["4. run_deterministic_analysis"]
    N4 --> N5["5. synthesize_explanation (Structured LLM or Deterministic Fallback)"]
    N5 --> N6["6. validate_grounding (Relational Anti-Fabrication Firewall)"]
    N6 --> R["Pydantic AssistantQueryResponse"]
```

### 3.2 Supported Operational Intents
1. `SKU_REJECTION_OR_CANDIDATE_EXPLANATION` — Explains exact feasible rankings and hard-constraint rejection codes for a specific SKU/Location.
2. `FAST_MOVER_RELOCATION_ANALYSIS` — Identifies dock-distant `FAST` movers with highest travel savings potential.
3. `TOP_EXCEPTIONS_ANALYSIS` — Summarizes open safety, capacity, co-location, blocked-aisle, and labor overload exceptions.
4. `REPLENISHMENT_WORKLOAD_ANALYSIS` — Analyzes per-warehouse shift labor utilization and overloaded tasks.
5. `PICKING_SAVINGS_ANALYSIS` — Compares baseline vs. optimized picking travel and fulfillment time across identical orders.
6. `WAREHOUSE_CAPACITY_UTILIZATION` — Evaluates slot occupancy, weight/volume utilization, and near-exhausted bays.
7. `DEMAND_FORECAST_AND_SURGE_ANALYSIS` — Reports MLflow Naive vs. ML WAPE/MAE metrics, surging SKUs, velocity shifts, and proactive slotting/ROP actions.
8. `LATEST_OPTIMIZATION_AND_EXECUTIVE_SUMMARY` — Synthesizes latest optimization run KPIs, assignment scores, and benchmark exception precision/recall/F1.

### 3.3 Structured Pydantic Validation, Source References & Anti-Fabrication Firewall
- **Structured Source References**: Every response populates `source_references: list[AssistantSourceReference]` (`entity_type`, `entity_id`, `source_table`, `summary`) and `cited_entity_ids` directly from SQL and ML artifact queries.
- **Optional Groq / OpenAI-Compatible Provider Adapter**: Configured via `LLM_PROVIDER=groq|openai|none` and `GROQ_API_KEY` / `OPENAI_API_KEY`. Validates LLM outputs strictly through `LLMIntentOutput` and `LLMSynthesisOutput`.
- **Automatic Deterministic Fallback (`DETERMINISTIC_GROUNDED_FALLBACK`)**: Whenever `LLM_PROVIDER=none`, an API key is absent, or a provider call fails, the assistant seamlessly returns deterministic SQL-grounded answers with zero degraded functionality.
- **Relational Anti-Fabrication Guard (`validate_grounding`)**: Scans `answer`, `key_findings`, `recommended_actions`, and `cited_entity_ids` across all 7 domain ID patterns (`SKU-*`, `LOC-*`, `WH-*`, `OPT-*`, `EXC-*`, `SCN-*`, `REP-*`) and checks every ID via `session.get(Model, id)`. If an LLM fabricates any non-existent ID, the firewall intercepts the response and reverts automatically to `DETERMINISTIC_FALLBACK_AFTER_GROUNDING_GUARD`.
