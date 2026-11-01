"""Comprehensive Phase 7 automated test suite: ML Demand Forecasting & LangGraph Warehouse Decision Assistant.

Verifies:
1. Exact MAE, RMSE, and WAPE (%) calculation and zero-division / negative-prediction safety.
2. Strict chronological train/test splits (`max(train_date) < min(test_date)`) and zero future data leakage.
3. Naive Baseline (`NAIVE_LAST_OBSERVATION`, `NAIVE_MOVING_AVG_7D`) vs. ML (`RIDGE_REGRESSION`,
   `HIST_GRADIENT_BOOSTING_POISSON`) evaluation, MLflow experiment tracking, and `.joblib` artifact persistence.
4. Opt-in integration of ML forecasts into Replenishment and Slotting Optimization without altering the
   default deterministic core system.
5. LangGraph Decision Assistant in zero-key `DETERMINISTIC_GROUNDED_FALLBACK` mode (including missing-key
   degradation when `llm_provider="groq"` or `"openai"` has no API key).
6. LangGraph Decision Assistant in AI-enabled structured LLM mode (`LLM_GROUNDED`) via LangChain `BaseChatModel`,
   Pydantic structured validation, source record references, and anti-fabrication relational grounding firewall
   (`DETERMINISTIC_FALLBACK_AFTER_GROUNDING_GUARD`).
7. FastAPI `/api/v1/ml/*` and `/api/v1/assistant/query` REST endpoint contracts and OpenAPI registration.
8. Optional live Groq/OpenAI provider verification when API credentials are present in the environment.
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from datetime import date
from pathlib import Path
from typing import Any

import joblib
import mlflow
import numpy as np
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.agents.assistant_service import DecisionAssistantService
from backend.app.agents.llm_adapter import LLMProviderAdapter
from backend.app.api.v1.assistant import query_decision_assistant
from backend.app.api.v1.ml import get_ml_status, list_sku_forecasts, train_ml_forecaster
from backend.app.config import DATA_DIR, EnvSettings, load_domain_config
from backend.app.database import create_db_engine
from backend.app.dependencies import EntityNotFoundError
from backend.app.main import create_app
from backend.app.ml.forecaster import FEATURE_COLUMNS, DemandForecastingService, compute_regression_metrics
from backend.app.models import OptimizationRun, SlotAssignment
from backend.app.optimization.solver import SlottingOptimizerService
from backend.app.repositories.warehouse_repo import WarehouseRepository
from backend.app.schemas.assistant import AssistantQueryRequest, AssistantQueryResponse
from backend.app.schemas.ml import MLTrainRequest
from backend.app.services.data_loader import DatabaseLoader
from backend.app.simulation.replenishment_simulator import ReplenishmentSimulationService


class StructuredGroundedMockChatModel(BaseChatModel):
    """Deterministic LangChain BaseChatModel emulating a valid structured Groq/OpenAI response."""

    @property
    def _llm_type(self) -> str:
        return "mock_structured_grounded_llm"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        sys_text = str(messages[0].content) if messages else ""
        user_text = str(messages[-1].content) if messages else ""

        if "intent router" in sys_text.lower():
            payload = {
                "intent": "DEMAND_FORECAST_AND_SURGE_ANALYSIS",
                "extracted_sku_id": None,
                "extracted_location_id": None,
                "extracted_warehouse_id": "WH-001",
            }
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=json.dumps(payload)))])

        # Parse verified SQL facts passed in HumanMessage so we cite strictly real database IDs
        facts = json.loads(user_text)
        allowed_ids: list[str] = list(facts.get("allowed_entity_ids") or ["WH-001", "SKU-000001"])
        top_ids = allowed_ids[:3] if allowed_ids else ["WH-001"]
        findings = list(facts.get("verified_findings") or ["Verified SQL demand forecast summary."])
        actions = list(facts.get("verified_recommended_actions") or ["Review forecast-guided slotting."])

        synthesis_payload = {
            "answer": (
                f"Structured AI synthesis grounded strictly on verified database records ({', '.join(top_ids)}): "
                f"{findings[0]}"
            ),
            "key_findings": findings[:3],
            "cited_entity_ids": top_ids,
            "recommended_actions": actions[:2],
        }
        return ChatResult(
            generations=[
                ChatGeneration(message=AIMessage(content=f"```json\n{json.dumps(synthesis_payload)}\n```"))
            ]
        )


class HallucinatingMockChatModel(BaseChatModel):
    """Adversarial LangChain BaseChatModel that fabricates non-existent SKU and Location IDs."""

    @property
    def _llm_type(self) -> str:
        return "mock_hallucinating_llm"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        sys_text = str(messages[0].content) if messages else ""
        if "intent router" in sys_text.lower():
            payload = {
                "intent": "TOP_EXCEPTIONS_ANALYSIS",
                "extracted_sku_id": None,
                "extracted_location_id": None,
                "extracted_warehouse_id": None,
            }
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=json.dumps(payload)))])

        # Fabricate non-existent operational IDs SKU-999999, LOC-Z999-999999, and EXC-2099-888888
        hallucinated_payload = {
            "answer": (
                "Critical alert: SKU-999999 stored in LOC-Z999-999999 triggered fabricated exception EXC-2099-888888 "
                "with 999,999 kg overload."
            ),
            "key_findings": ["Fabricated finding referencing non-existent SKU-999999 and LOC-Z999-999999."],
            "cited_entity_ids": ["SKU-999999", "LOC-Z999-999999", "EXC-2099-888888"],
            "recommended_actions": ["Move SKU-999999 immediately."],
        }
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=json.dumps(hallucinated_payload)))]
        )


@pytest.fixture(scope="module")
def phase7_ctx(tmp_path_factory: pytest.TempPathFactory) -> Generator[dict[str, Any], None, None]:
    """Provision an isolated SQLite DB + isolated artifacts root loaded with the `small` dataset."""
    tmp_dir = tmp_path_factory.mktemp("phase7_ml_agents")
    db_path = tmp_dir / "phase7.db"
    artifacts_dir = tmp_dir / "artifacts"

    engine = create_db_engine(f"sqlite:///{db_path.as_posix()}")
    loader = DatabaseLoader(engine=engine)
    loader.load_profile_from_disk(profile="small", data_root=DATA_DIR, reset_existing=True)

    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )
    cfg = load_domain_config(profile="small")
    forecaster = DemandForecastingService(config=cfg, artifacts_root=artifacts_dir)

    yield {
        "tmp_dir": tmp_dir,
        "artifacts_dir": artifacts_dir,
        "engine": engine,
        "session_factory": session_factory,
        "config": cfg,
        "forecaster": forecaster,
    }
    engine.dispose()


def test_regression_metrics_mae_rmse_wape_exact_and_zero_safe() -> None:
    """Verify exact MAE, RMSE, and WAPE (%) computation, negative clipping, and zero-denominator protection."""
    y_true = np.array([10.0, 20.0, 0.0, 10.0])
    y_pred = np.array([12.0, 18.0, 4.0, -2.0])  # -2.0 clipped to 0.0 -> abs errors: 2, 2, 4, 10 (sum=18, mean=4.5)

    metrics = compute_regression_metrics(y_true, y_pred)
    assert metrics["mae"] == pytest.approx(4.5, abs=1e-4)
    # Squared errors: 4 + 4 + 16 + 100 = 124 -> mean = 31 -> sqrt(31) = 5.5678
    assert metrics["rmse"] == pytest.approx(float(np.sqrt(31.0)), abs=1e-4)
    # WAPE = 18 / 40 * 100 = 45.0%
    assert metrics["wape_pct"] == pytest.approx(45.0, abs=1e-4)

    zero_metrics = compute_regression_metrics(np.zeros(5), np.zeros(5))
    assert zero_metrics == {"mae": 0.0, "rmse": 0.0, "wape_pct": 0.0}


def test_chronological_split_and_causal_features_zero_future_leakage(phase7_ctx: dict[str, Any]) -> None:
    """Prove chronological split ordering and verify post-cutoff future mutations never leak into pre-cutoff features."""
    forecaster: DemandForecastingService = phase7_ctx["forecaster"]
    session_factory = phase7_ctx["session_factory"]

    with session_factory() as session:
        panel_df, sku_meta, all_dates = forecaster.build_dense_daily_panel(session)

    assert len(sku_meta) == 250
    assert len(all_dates) == 90
    assert len(panel_df) == 250 * 90

    cutoff_dt: date = all_dates[71]  # 80% chronological cutoff
    feat_clean, priors_clean = forecaster.engineer_causal_features(panel_df, cutoff_date=cutoff_dt)

    # Inject a massive future demand spike strictly AFTER cutoff_dt (+50,000 units on all post-cutoff days)
    corrupted_future_panel = panel_df.copy()
    future_mask = corrupted_future_panel["order_date"] > cutoff_dt
    assert int(future_mask.sum()) > 0
    corrupted_future_panel.loc[future_mask, "units_ordered"] += 50_000.0

    feat_mutated, priors_mutated = forecaster.engineer_causal_features(
        corrupted_future_panel, cutoff_date=cutoff_dt
    )

    # 1. Every training-window feature row (order_date <= cutoff_dt) MUST be bit-for-bit identical
    train_clean = feat_clean[feat_clean["order_date"] <= cutoff_dt][FEATURE_COLUMNS].to_numpy(dtype=np.float64)
    train_mutated = feat_mutated[feat_mutated["order_date"] <= cutoff_dt][FEATURE_COLUMNS].to_numpy(
        dtype=np.float64
    )
    np.testing.assert_allclose(train_clean, train_mutated, rtol=0.0, atol=1e-12)

    # 2. Even the first holdout day (cutoff_dt + 1) uses strictly causal lags <= cutoff_dt, so it is also identical!
    first_test_dt = all_dates[72]
    day1_clean = feat_clean[feat_clean["order_date"] == first_test_dt][FEATURE_COLUMNS].to_numpy(dtype=np.float64)
    day1_mutated = feat_mutated[feat_mutated["order_date"] == first_test_dt][FEATURE_COLUMNS].to_numpy(
        dtype=np.float64
    )
    np.testing.assert_allclose(day1_clean, day1_mutated, rtol=0.0, atol=1e-12)

    # 3. Per-SKU priors are computed strictly within `order_date <= cutoff_dt`
    assert priors_clean == priors_mutated


def test_ml_training_naive_vs_ml_comparison_mlflow_tracking_and_artifacts(phase7_ctx: dict[str, Any]) -> None:
    """Train forecasters, verify chronological split, Naive vs. ML leaderboard, MLflow tracking, and saved artifacts."""
    forecaster: DemandForecastingService = phase7_ctx["forecaster"]
    session_factory = phase7_ctx["session_factory"]

    with session_factory() as session:
        summary = forecaster.train_and_evaluate(
            session=session,
            profile="small",
            forecast_horizon_days=14,
            train_ratio=0.80,
            actor="PYTEST_PHASE7",
        )
        session.commit()

    assert summary["status"] == "COMPLETED"
    split = summary["split_info"]
    assert split["chronological_order_verified"] is True
    assert split["train_end_date"] < split["test_start_date"]
    assert split["train_rows"] > 0 and split["test_rows"] > 0

    # Verify leaderboard contains all Naive Baselines and both ML models
    lb_by_name = {row["model_name"]: row for row in summary["leaderboard"]}
    assert set(lb_by_name.keys()) == {
        "NAIVE_ZERO_FORECAST",
        "NAIVE_SKU_TRAIN_MEAN",
        "NAIVE_LAST_OBSERVATION",
        "NAIVE_MOVING_AVG_7D",
        "RIDGE_REGRESSION",
        "HIST_GRADIENT_BOOSTING_POISSON",
    }

    champion = next(row for row in summary["leaderboard"] if row["is_champion"])
    assert champion["model_family"] == "MACHINE_LEARNING"
    assert champion["daily_mae"] < lb_by_name["NAIVE_LAST_OBSERVATION"]["daily_mae"]
    assert champion["daily_rmse"] < lb_by_name["NAIVE_LAST_OBSERVATION"]["daily_rmse"]
    assert champion["daily_wape_pct"] < lb_by_name["NAIVE_LAST_OBSERVATION"]["daily_wape_pct"]
    assert champion["daily_wape_pct"] <= lb_by_name["NAIVE_MOVING_AVG_7D"]["daily_wape_pct"]
    assert champion["horizon_wape_pct"] < lb_by_name["NAIVE_LAST_OBSERVATION"]["horizon_wape_pct"]

    # Verify MLflow experiment & logged runs exist in local SQLite tracking store
    mlflow.set_tracking_uri(summary["mlflow_tracking_uri"])
    exp = mlflow.get_experiment_by_name(summary["mlflow_experiment_name"])
    assert exp is not None
    runs_df = mlflow.search_runs(experiment_ids=[exp.experiment_id])
    assert len(runs_df) >= 6
    assert "metrics.daily_mae" in runs_df.columns
    assert "metrics.daily_rmse" in runs_df.columns
    assert "metrics.daily_wape_pct" in runs_df.columns

    # Verify persisted `.joblib` model bundle and JSON forecast artifacts
    joblib_path = Path(summary["artifact_joblib_path"])
    eval_path = Path(summary["evaluation_report_path"])
    forecasts_path = Path(summary["forecasts_path"])
    assert joblib_path.exists()
    assert eval_path.exists()
    assert forecasts_path.exists()

    loaded_bundle = joblib.load(joblib_path)
    assert loaded_bundle["champion_model_name"] == summary["champion_model_name"]
    assert loaded_bundle["feature_columns"] == FEATURE_COLUMNS
    sample_x = np.zeros((3, len(FEATURE_COLUMNS)), dtype=np.float64)
    preds = np.maximum(0.0, loaded_bundle["production_estimator"].predict(sample_x))
    assert preds.shape == (3,)
    assert np.all(np.isfinite(preds))

    forecasts = forecaster.list_forecasts(profile="small")
    assert len(forecasts) == 250
    sample_fc = forecasts[0]
    assert len(sample_fc["daily_forecast_trajectory"]) == 14
    assert sample_fc["slotting_recommendation"] in {
        "PROMOTE_TO_DOCK_PROXIMATE_FAST_PICK",
        "REALLOCATE_TO_GENERAL_OR_RESERVE",
        "MAINTAIN_CURRENT_VELOCITY_ZONE",
    }
    assert sample_fc["replenishment_recommendation"] in {
        "INCREASE_ROP_PROACTIVE_REPLENISHMENT",
        "TRIM_SAFETY_BUFFER_REDUCE_CYCLE_STOCK",
        "MAINTAIN_STANDARD_REORDER_POLICY",
    }


def test_forecast_integration_preserves_deterministic_core_and_guides_slotting_and_replenishment(
    phase7_ctx: dict[str, Any],
) -> None:
    """Prove default `use_ml_forecasts=False` leaves deterministic core intact while `True` integrates forecasts."""
    cfg = phase7_ctx["config"]
    forecaster: DemandForecastingService = phase7_ctx["forecaster"]
    session_factory = phase7_ctx["session_factory"]

    rep_service = ReplenishmentSimulationService(config=cfg)
    opt_service = SlottingOptimizerService(config=cfg)

    with session_factory() as session:
        # 1. Replenishment deterministic core invariance
        det_rep_a = rep_service.simulate_replenishment(
            session=session, use_ml_forecasts=False, persist_events=False
        )
        det_rep_b = rep_service.simulate_replenishment(
            session=session, use_ml_forecasts=False, persist_events=False
        )
        assert det_rep_a["demand_source"] == "HISTORICAL_OBSERVED"
        assert det_rep_a["forecast_adjusted_sku_count"] == 0
        assert det_rep_a == det_rep_b

        # 2. Replenishment ML forecast-guided simulation
        active_assns = session.scalars(
            select(SlotAssignment).where(SlotAssignment.assignment_status == "ACTIVE")
        ).all()
        ml_rep = rep_service.simulate_replenishment(
            session=session,
            use_ml_forecasts=True,
            ml_forecaster=forecaster,
            persist_events=False,
        )
        assert ml_rep["demand_source"] == "ML_FORECAST"
        assert ml_rep["forecast_adjusted_sku_count"] == len(active_assns) == 248

        # 3. Slotting Optimization: deterministic vs. forecast-guided on surging/shifted SKU subset
        shifted_skus = [
            f["sku_id"] for f in forecaster.list_forecasts(profile="small") if f["velocity_tier_changed"]
        ][:12]
        if len(shifted_skus) < 5:
            shifted_skus = [f"SKU-{i:06d}" for i in range(1, 11)]

        det_opt = opt_service.run_optimization(
            session=session,
            sku_ids=shifted_skus,
            strategy="greedy",
            apply_immediately=False,
            use_ml_forecasts=False,
            actor="PYTEST_DET_OPT",
        )
        det_run_row = session.get(OptimizationRun, det_opt.run_id)
        assert det_run_row is not None
        assert det_run_row.kpi_results_json["demand_source"] == "HISTORICAL_OBSERVED"
        assert det_run_row.kpi_results_json["forecast_guided_skus"] == 0
        assert det_run_row.kpi_results_json["constraint_violation_count"] == 0

        ml_opt = opt_service.run_optimization(
            session=session,
            sku_ids=shifted_skus,
            strategy="greedy",
            apply_immediately=False,
            use_ml_forecasts=True,
            ml_forecaster=forecaster,
            actor="PYTEST_ML_OPT",
        )
        ml_run_row = session.get(OptimizationRun, ml_opt.run_id)
        assert ml_run_row is not None
        assert ml_run_row.kpi_results_json["demand_source"] == "ML_FORECAST"
        assert ml_run_row.kpi_results_json["forecast_guided_skus"] == len(shifted_skus)
        assert ml_run_row.kpi_results_json["constraint_violation_count"] == 0

        ml_assns = session.scalars(
            select(SlotAssignment).where(SlotAssignment.optimization_run_id == ml_opt.run_id)
        ).all()
        assert len(ml_assns) > 0
        assert all("[ML Forecast ADV=" in a.decision_reason for a in ml_assns)
        session.rollback()


def test_langgraph_decision_assistant_deterministic_fallback_without_api_key(phase7_ctx: dict[str, Any]) -> None:
    """Verify 100% functionality without any LLM API key across deterministic and missing-key fallback configurations."""
    cfg = phase7_ctx["config"]
    forecaster: DemandForecastingService = phase7_ctx["forecaster"]
    session_factory = phase7_ctx["session_factory"]

    # Case A: Default zero-key mode (`llm_provider="none"`)
    no_llm_settings = EnvSettings(llm_provider="none", groq_api_key="", openai_api_key="")
    assistant = DecisionAssistantService(config=cfg, ml_forecaster=forecaster)

    with session_factory() as session:
        resp_fc = assistant.answer_query(
            session=session,
            question="Which SKUs have surging ML demand forecasts and what is our forecast WAPE?",
            settings_override=no_llm_settings,
        )
        assert isinstance(resp_fc, AssistantQueryResponse)
        assert resp_fc.classified_intent == "DEMAND_FORECAST_AND_SURGE_ANALYSIS"
        assert resp_fc.execution_mode == "DETERMINISTIC_GROUNDED_FALLBACK"
        assert resp_fc.grounding_verified is True
        assert len(resp_fc.key_findings) > 0
        assert len(resp_fc.recommended_actions) > 0
        assert len(resp_fc.source_references) > 0
        assert [t.node_name for t in resp_fc.trace] == [
            "classify_intent",
            "retrieve_warehouse_context",
            "retrieve_kpi_and_exceptions",
            "run_deterministic_analysis",
            "synthesize_explanation",
            "validate_grounding",
        ]

        # Case B: Provider set to "groq" WITHOUT an API key -> must degrade cleanly without raising an error
        missing_groq_settings = EnvSettings(llm_provider="groq", groq_api_key="", openai_api_key="")
        resp_sku = assistant.answer_query(
            session=session,
            question="Why was SKU-000001 placed or rejected across candidate locations?",
            settings_override=missing_groq_settings,
        )
        assert resp_sku.classified_intent == "SKU_REJECTION_OR_CANDIDATE_EXPLANATION"
        assert resp_sku.execution_mode == "DETERMINISTIC_GROUNDED_FALLBACK"
        assert resp_sku.grounding_verified is True
        assert "SKU-000001" in resp_sku.cited_entity_ids
        assert any("fallback_reason=missing_groq_api_key" in t.summary for t in resp_sku.trace)


def test_langgraph_decision_assistant_ai_enabled_structured_llm_and_anti_fabrication_guard(
    phase7_ctx: dict[str, Any],
) -> None:
    """Test AI-enabled structured LLM synthesis AND verify the anti-fabrication firewall blocks hallucinated IDs."""
    cfg = phase7_ctx["config"]
    forecaster: DemandForecastingService = phase7_ctx["forecaster"]
    session_factory = phase7_ctx["session_factory"]

    # 1. Valid structured AI-enabled mode (`LLM_GROUNDED:MOCK_GROQ`)
    grounded_adapter = LLMProviderAdapter(
        chat_model=StructuredGroundedMockChatModel(),
        provider_label_override="MOCK_GROQ",
    )
    assert grounded_adapter.is_available is True
    ai_assistant = DecisionAssistantService(
        config=cfg,
        llm_adapter=grounded_adapter,
        ml_forecaster=forecaster,
    )

    with session_factory() as session:
        ai_resp = ai_assistant.answer_query(
            session=session,
            question="Give me a forecast surge analysis for WH-001.",
        )
        assert ai_resp.execution_mode == "LLM_GROUNDED:MOCK_GROQ"
        assert ai_resp.classified_intent == "DEMAND_FORECAST_AND_SURGE_ANALYSIS"
        assert ai_resp.grounding_verified is True
        assert "Structured AI synthesis grounded strictly on verified database records" in ai_resp.answer
        assert len(ai_resp.source_references) > 0

        # 2. Adversarial hallucinating LLM citing non-existent SKU-999999, LOC-Z999-999999, EXC-2099-888888
        hallucinating_adapter = LLMProviderAdapter(
            chat_model=HallucinatingMockChatModel(),
            provider_label_override="ADVERSARIAL_LLM",
        )
        guarded_assistant = DecisionAssistantService(
            config=cfg,
            llm_adapter=hallucinating_adapter,
            ml_forecaster=forecaster,
        )
        guarded_resp = guarded_assistant.answer_query(
            session=session,
            question="Summarize open operational exceptions.",
        )

        # Firewall MUST intercept fabricated IDs and revert to SQL-verified deterministic output
        assert guarded_resp.execution_mode == "DETERMINISTIC_FALLBACK_AFTER_GROUNDING_GUARD"
        assert guarded_resp.grounding_verified is True
        assert "SKU-999999" not in guarded_resp.answer
        assert "LOC-Z999-999999" not in guarded_resp.answer
        assert "EXC-2099-888888" not in guarded_resp.answer
        assert "SKU-999999" not in guarded_resp.cited_entity_ids
        assert "LOC-Z999-999999" not in guarded_resp.cited_entity_ids
        assert any("Intercepted fabricated entity ID(s)" in t.summary for t in guarded_resp.trace)


def test_phase7_fastapi_ml_and_assistant_endpoints(phase7_ctx: dict[str, Any]) -> None:
    """Verify FastAPI OpenAPI registration and `/api/v1/ml/*` + `/api/v1/assistant/query` route contracts."""
    app = create_app()
    paths = set(app.openapi()["paths"].keys())
    assert "/api/v1/ml/status" in paths
    assert "/api/v1/ml/train" in paths
    assert "/api/v1/ml/forecasts" in paths
    assert "/api/v1/assistant/query" in paths

    forecaster: DemandForecastingService = phase7_ctx["forecaster"]
    cfg = phase7_ctx["config"]
    session_factory = phase7_ctx["session_factory"]
    settings = EnvSettings(config_profile="small", enable_ml=True, llm_provider="none")
    wh_repo = WarehouseRepository()

    status_out = get_ml_status(profile="small", settings=settings, forecaster=forecaster)
    assert status_out.enabled is True
    assert status_out.trained_artifacts_present is True
    assert status_out.evaluation_summary is not None

    with session_factory() as session:
        fc_page = list_sku_forecasts(
            sku_id=None,
            warehouse_id="WH-001",
            velocity_shift_only=False,
            surging_only=False,
            sort_by="surge_ratio",
            sort_order="desc",
            page=1,
            page_size=20,
            db=session,
            settings=settings,
            forecaster=forecaster,
            wh_repo=wh_repo,
        )
        assert fc_page.meta.total_items > 0
        assert len(fc_page.items) <= 20
        assert all(item.active_warehouse_id == "WH-001" for item in fc_page.items)

        with pytest.raises(EntityNotFoundError):
            list_sku_forecasts(
                sku_id=None,
                warehouse_id="WH-999",
                velocity_shift_only=False,
                surging_only=False,
                sort_by="surge_ratio",
                sort_order="desc",
                page=1,
                page_size=20,
                db=session,
                settings=settings,
                forecaster=forecaster,
                wh_repo=wh_repo,
            )

        train_resp = train_ml_forecaster(
            payload=MLTrainRequest(profile="small", forecast_horizon_days=7, train_ratio=0.80),
            actor="PYTEST_API",
            db=session,
            settings=settings,
            forecaster=forecaster,
        )
        assert train_resp.status == "COMPLETED"
        assert len(train_resp.leaderboard) == 6

        assistant = DecisionAssistantService(config=cfg, ml_forecaster=forecaster)
        asst_resp = query_decision_assistant(
            payload=AssistantQueryRequest(
                question="Summarize picking distance savings across identical orders.",
                warehouse_id="WH-001",
            ),
            db=session,
            settings=settings,
            assistant=assistant,
            wh_repo=wh_repo,
        )
        assert asst_resp.classified_intent == "PICKING_SAVINGS_ANALYSIS"
        assert asst_resp.grounding_verified is True
        assert len(asst_resp.source_references) > 0


@pytest.mark.skipif(
    not (os.getenv("GROQ_API_KEY") or os.getenv("OPENAI_API_KEY")),
    reason="No live GROQ_API_KEY or OPENAI_API_KEY configured in host environment.",
)
def test_optional_live_llm_provider_when_credentials_present(phase7_ctx: dict[str, Any]) -> None:
    """Automatically executes against live Groq or OpenAI credentials if present in the environment."""
    cfg = phase7_ctx["config"]
    forecaster: DemandForecastingService = phase7_ctx["forecaster"]
    session_factory = phase7_ctx["session_factory"]

    provider = "groq" if os.getenv("GROQ_API_KEY") else "openai"
    model_name = "llama-3.3-70b-versatile" if provider == "groq" else "gpt-4o-mini"
    live_settings = EnvSettings(
        llm_provider=provider,
        llm_model=model_name,
        groq_api_key=os.getenv("GROQ_API_KEY", ""),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
    )
    assistant = DecisionAssistantService(config=cfg, ml_forecaster=forecaster)
    with session_factory() as session:
        resp = assistant.answer_query(
            session=session,
            question="What are the top open exceptions and recommended actions?",
            settings_override=live_settings,
        )
        assert resp.grounding_verified is True
        assert resp.execution_mode.startswith("LLM_GROUNDED:") or resp.execution_mode in {
            "DETERMINISTIC_FALLBACK_AFTER_GROUNDING_GUARD",
            "DETERMINISTIC_GROUNDED_FALLBACK",
        }
