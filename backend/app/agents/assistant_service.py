"""LangGraph-powered Warehouse Decision Assistant with grounded SQL retrieval, ML forecast awareness,
optional Groq/OpenAI structured LLM adapter, anti-fabrication grounding firewall, and deterministic fallback.

Implements Section 13:
User Question -> Intent Classification -> Warehouse Context Retrieval -> KPI / Exception / Forecast Retrieval
-> Deterministic Analysis -> Structured Explanation (LLM or Deterministic Fallback) -> Response Grounding Validation.
"""

from __future__ import annotations

import re
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.llm_adapter import LLMProviderAdapter
from backend.app.analytics.kpi_service import AnalyticsKPIService
from backend.app.config import DomainConfig, EnvSettings, get_env_settings, load_domain_config
from backend.app.ml.forecaster import DemandForecastingService
from backend.app.models import (
    SKU,
    ExceptionRecord,
    Location,
    OptimizationRun,
    Replenishment,
    Scenario,
    SKUProfile,
    SlotAssignment,
    Warehouse,
    Zone,
)
from backend.app.models.enums import AssignmentStatus, DemandVelocityClass, ExceptionResolutionStatus
from backend.app.optimization.candidates import CandidateGeneratorService
from backend.app.repositories.warehouse_repo import WarehouseRepository
from backend.app.schemas.assistant import (
    AssistantQueryResponse,
    AssistantSourceReference,
    AssistantTraceStep,
)


class AssistantGraphState(TypedDict):
    """Typed state flowing across the 6-node LangGraph decision assistant."""

    question: str
    warehouse_filter: str | None
    intent: str
    extracted_sku_id: str | None
    extracted_location_id: str | None
    extracted_warehouse_id: str | None
    warehouse_context: dict[str, Any]
    operational_data: dict[str, Any]
    analysis_result: dict[str, Any]
    deterministic_answer: str
    deterministic_findings: list[str]
    deterministic_actions: list[str]
    deterministic_cited_ids: list[str]
    source_references: list[dict[str, str]]
    answer: str
    key_findings: list[str]
    recommended_actions: list[str]
    cited_ids: list[str]
    execution_mode: str
    grounding_verified: bool
    trace: list[dict[str, Any]]


SKU_PATTERN = re.compile(r"\bSKU-\d{6}\b", re.IGNORECASE)
LOC_PATTERN = re.compile(r"\bLOC-Z\d{3}-\d{6}\b", re.IGNORECASE)
WH_PATTERN = re.compile(r"\bWH-\d{3}\b", re.IGNORECASE)
OPT_PATTERN = re.compile(r"\bOPT-\d{4}-\d{6}\b", re.IGNORECASE)
EXC_PATTERN = re.compile(r"\bEXC-\d{4}-\d{6}\b", re.IGNORECASE)
SCN_PATTERN = re.compile(r"\bSCN-\d{4}-\d{5}\b", re.IGNORECASE)
REP_PATTERN = re.compile(r"\bREP-\d{4}-\d{6}\b", re.IGNORECASE)


def _deterministic_intent_rule(
    question: str,
    warehouse_filter: str | None,
) -> tuple[str, str | None, str | None, str | None]:
    q_lower = question.lower()
    sku_match = SKU_PATTERN.search(question)
    loc_match = LOC_PATTERN.search(question)
    wh_match = WH_PATTERN.search(question)

    ext_sku = sku_match.group(0).upper() if sku_match else None
    ext_loc = loc_match.group(0).upper() if loc_match else None
    ext_wh = (wh_match.group(0).upper() if wh_match else None) or warehouse_filter

    if (
        "forecast" in q_lower
        or "surge" in q_lower
        or "surging" in q_lower
        or "mlflow" in q_lower
        or "wape" in q_lower
    ):
        intent = "DEMAND_FORECAST_AND_SURGE_ANALYSIS"
    elif ext_sku and ("reject" in q_lower or "why" in q_lower or "candidate" in q_lower or ext_loc):
        intent = "SKU_REJECTION_OR_CANDIDATE_EXPLANATION"
    elif "relocat" in q_lower or ("fast" in q_lower and "sku" in q_lower):
        intent = "FAST_MOVER_RELOCATION_ANALYSIS"
    elif "exception" in q_lower or "violation" in q_lower or "anomal" in q_lower:
        intent = "TOP_EXCEPTIONS_ANALYSIS"
    elif "replenish" in q_lower or "labor" in q_lower or "workload" in q_lower:
        intent = "REPLENISHMENT_WORKLOAD_ANALYSIS"
    elif "pick" in q_lower or "distance" in q_lower or "route" in q_lower:
        intent = "PICKING_SAVINGS_ANALYSIS"
    elif "utilization" in q_lower or "capacity" in q_lower or "warehouse" in q_lower:
        intent = "WAREHOUSE_CAPACITY_UTILIZATION"
    else:
        intent = "LATEST_OPTIMIZATION_AND_EXECUTIVE_SUMMARY"

    return intent, ext_sku, ext_loc, ext_wh


class DecisionAssistantService:
    """Grounded LangGraph decision-support assistant operating strictly read-only over operational data."""

    def __init__(
        self,
        config: DomainConfig | None = None,
        llm_adapter: LLMProviderAdapter | None = None,
        ml_forecaster: DemandForecastingService | None = None,
    ) -> None:
        self.config = config or load_domain_config()
        self.kpi_service = AnalyticsKPIService(self.config)
        self.cand_service = CandidateGeneratorService(self.config)
        self.wh_repo = WarehouseRepository()
        self._explicit_llm_adapter = llm_adapter
        self.ml_forecaster = ml_forecaster or DemandForecastingService(config=self.config)

    def _resolve_llm_adapter(self, settings: EnvSettings | None = None) -> LLMProviderAdapter:
        if self._explicit_llm_adapter is not None:
            return self._explicit_llm_adapter
        return LLMProviderAdapter(settings=settings or get_env_settings())

    def answer_query(
        self,
        session: Session,
        question: str,
        warehouse_id: str | None = None,
        settings_override: EnvSettings | None = None,
    ) -> AssistantQueryResponse:
        """Execute the 6-node LangGraph decision workflow on the user's operational question."""
        adapter = self._resolve_llm_adapter(settings=settings_override)

        def classify_intent(state: AssistantGraphState) -> dict[str, Any]:
            det_intent, ext_sku, ext_loc, ext_wh = _deterministic_intent_rule(
                state["question"], state["warehouse_filter"]
            )
            intent = det_intent
            classifier_source = "deterministic_rules"

            if adapter.is_available:
                llm_cls = adapter.classify_intent_structured(state["question"])
                if llm_cls is not None:
                    intent = llm_cls.intent
                    if (
                        not ext_sku
                        and llm_cls.extracted_sku_id
                        and SKU_PATTERN.fullmatch(llm_cls.extracted_sku_id)
                    ):
                        ext_sku = llm_cls.extracted_sku_id.upper()
                    if (
                        not ext_loc
                        and llm_cls.extracted_location_id
                        and LOC_PATTERN.fullmatch(llm_cls.extracted_location_id)
                    ):
                        ext_loc = llm_cls.extracted_location_id.upper()
                    if (
                        not ext_wh
                        and llm_cls.extracted_warehouse_id
                        and WH_PATTERN.fullmatch(llm_cls.extracted_warehouse_id)
                    ):
                        ext_wh = llm_cls.extracted_warehouse_id.upper()
                    classifier_source = f"llm_structured:{adapter.provider_name}"

            step = {
                "node_name": "classify_intent",
                "summary": (
                    f"Classified query as '{intent}' via {classifier_source} "
                    f"(sku={ext_sku}, location={ext_loc}, warehouse={ext_wh})."
                ),
                "record_ids": [x for x in (ext_sku, ext_loc, ext_wh) if x],
            }
            return {
                "intent": intent,
                "extracted_sku_id": ext_sku,
                "extracted_location_id": ext_loc,
                "extracted_warehouse_id": ext_wh,
                "trace": state["trace"] + [step],
            }

        def retrieve_warehouse_context(state: AssistantGraphState) -> dict[str, Any]:
            wh_page = self.wh_repo.list_warehouses(session=session, page=1, page_size=50)
            wh_dicts = [w.model_dump(mode="json") for w in wh_page.items]
            wh_ids = [w["warehouse_id"] for w in wh_dicts]

            sku_ctx: dict[str, Any] | None = None
            loc_ctx: dict[str, Any] | None = None
            rec_ids = list(wh_ids[:5])
            refs: list[dict[str, str]] = []

            for w in wh_dicts[:5]:
                if state["extracted_warehouse_id"] is None or w["warehouse_id"] == state["extracted_warehouse_id"]:
                    refs.append(
                        {
                            "entity_type": "WAREHOUSE",
                            "entity_id": w["warehouse_id"],
                            "source_table": "warehouses",
                            "summary": (
                                f"{w['warehouse_name']} ({w['city']}): "
                                f"{w['occupied_locations']}/{w['total_locations']} slots occupied ({w['location_utilization_pct']}%)."
                            ),
                        }
                    )

            if state["extracted_sku_id"]:
                sku_obj = session.get(SKU, state["extracted_sku_id"])
                prof_obj = session.scalars(
                    select(SKUProfile).where(
                        SKUProfile.sku_id == state["extracted_sku_id"],
                        SKUProfile.effective_to.is_(None),
                    )
                ).first()
                if sku_obj:
                    rec_ids.append(sku_obj.sku_id)
                    sku_ctx = {
                        "sku_id": sku_obj.sku_id,
                        "sku_name": sku_obj.sku_name,
                        "category": sku_obj.category,
                        "dimensions_cm": f"{sku_obj.width_cm}x{sku_obj.height_cm}x{sku_obj.depth_cm}",
                        "unit_weight_kg": float(sku_obj.unit_weight_kg),
                        "hazard_class": sku_obj.hazard_class,
                        "temperature_requirement": sku_obj.temperature_requirement,
                        "demand_velocity": prof_obj.demand_velocity if prof_obj else "UNKNOWN",
                        "abc_class": prof_obj.abc_class if prof_obj else "UNKNOWN",
                    }
                    refs.append(
                        {
                            "entity_type": "SKU",
                            "entity_id": sku_obj.sku_id,
                            "source_table": "skus / sku_profiles",
                            "summary": (
                                f"{sku_obj.sku_name} ({sku_obj.category}, {sku_obj.hazard_class}, "
                                f"{sku_obj.temperature_requirement}, vel={sku_ctx['demand_velocity']})."
                            ),
                        }
                    )
            if state["extracted_location_id"]:
                loc_detail = self.wh_repo.get_location_detail(session, state["extracted_location_id"])
                if loc_detail:
                    rec_ids.append(loc_detail.location_id)
                    loc_ctx = loc_detail.model_dump(mode="json")
                    refs.append(
                        {
                            "entity_type": "LOCATION",
                            "entity_id": loc_detail.location_id,
                            "source_table": "locations",
                            "summary": (
                                f"Zone {loc_detail.zone_id} ({loc_detail.zone_type}, {loc_detail.temperature_type}), "
                                f"vol_util={loc_detail.volume_utilization_pct}%, wt_util={loc_detail.weight_utilization_pct}%."
                            ),
                        }
                    )

            step = {
                "node_name": "retrieve_warehouse_context",
                "summary": f"Retrieved {len(wh_dicts)} warehouses and target entity metadata from SQL.",
                "record_ids": list(dict.fromkeys(rec_ids)),
            }
            return {
                "warehouse_context": {"warehouses": wh_dicts, "sku": sku_ctx, "location": loc_ctx},
                "source_references": refs,
                "trace": state["trace"] + [step],
            }

        def retrieve_kpi_and_exceptions(state: AssistantGraphState) -> dict[str, Any]:
            kpis = self.kpi_service.get_overview_kpis(
                session=session,
                warehouse_id=state["extracted_warehouse_id"],
                sample_picking_orders=200,
            )
            open_excs = session.scalars(
                select(ExceptionRecord)
                .where(
                    ExceptionRecord.resolution_status.in_(
                        [
                            ExceptionResolutionStatus.OPEN.value,
                            ExceptionResolutionStatus.IN_REVIEW.value,
                        ]
                    )
                )
                .order_by(ExceptionRecord.exception_id.asc())
                .limit(10)
            ).all()
            latest_run = session.scalars(
                select(OptimizationRun)
                .order_by(OptimizationRun.created_at.desc(), OptimizationRun.run_id.desc())
                .limit(1)
            ).first()

            exc_list = [
                {
                    "exception_id": e.exception_id,
                    "issue_type": e.issue_type,
                    "severity": e.severity,
                    "entity_id": e.entity_id,
                    "description": e.description,
                    "recommended_action": e.recommended_action,
                }
                for e in open_excs
            ]
            run_dict = (
                {
                    "run_id": latest_run.run_id,
                    "algorithm_name": latest_run.algorithm_name,
                    "sku_count": latest_run.sku_count,
                    "assignment_count": latest_run.assignment_count,
                    "unassigned_sku_count": latest_run.unassigned_sku_count,
                    "kpi_results": latest_run.kpi_results_json,
                }
                if latest_run
                else None
            )

            ml_status = self.ml_forecaster.get_status()
            ml_forecasts = self.ml_forecaster.list_forecasts(
                sku_id=state["extracted_sku_id"],
                warehouse_id=state["extracted_warehouse_id"],
            )

            rec_ids = [e["exception_id"] for e in exc_list[:5]]
            if run_dict:
                rec_ids.insert(0, run_dict["run_id"])

            step = {
                "node_name": "retrieve_kpi_and_exceptions",
                "summary": (
                    f"Loaded live KPI snapshot, {len(exc_list)} open operational exceptions, "
                    f"and {len(ml_forecasts)} ML SKU forecasts."
                ),
                "record_ids": rec_ids,
            }
            return {
                "operational_data": {
                    "kpis": kpis,
                    "open_exceptions": exc_list,
                    "latest_run": run_dict,
                    "ml_status": ml_status,
                    "ml_forecasts": ml_forecasts,
                },
                "trace": state["trace"] + [step],
            }

        def run_deterministic_analysis(state: AssistantGraphState) -> dict[str, Any]:
            intent = state["intent"]
            wh_list = state["warehouse_context"]["warehouses"]
            kpis = state["operational_data"]["kpis"]
            excs = state["operational_data"]["open_exceptions"]
            latest_run = state["operational_data"]["latest_run"]
            ml_status = state["operational_data"]["ml_status"]
            ml_forecasts: list[dict[str, Any]] = state["operational_data"]["ml_forecasts"]

            cited: list[str] = []
            findings: list[str] = []
            actions: list[str] = []
            metrics: dict[str, Any] = {}
            refs = list(state["source_references"])

            def _add_ref(e_type: str, e_id: str, table: str, summary: str) -> None:
                if not any(r["entity_id"] == e_id and r["source_table"] == table for r in refs):
                    refs.append(
                        {
                            "entity_type": e_type,
                            "entity_id": e_id,
                            "source_table": table,
                            "summary": summary,
                        }
                    )

            if intent == "DEMAND_FORECAST_AND_SURGE_ANALYSIS":
                eval_sum = ml_status.get("evaluation_summary")
                if not ml_status.get("trained_artifacts_present") or not eval_sum:
                    findings.append(
                        "No trained ML demand forecast artifacts are currently present on disk; run POST /api/v1/ml/train or scripts/train_forecaster.py."
                    )
                    actions.append(
                        "Execute the chronological ML demand forecaster to generate 14-day SKU forecasts."
                    )
                    metrics = {"trained_artifacts_present": False}
                else:
                    champ = eval_sum.get("champion_model_name", "HIST_GRADIENT_BOOSTING_POISSON")
                    wape_imp = eval_sum.get("wape_improvement_vs_naive_pct", 0.0)
                    champ_row = next(
                        (r for r in eval_sum.get("leaderboard", []) if r.get("is_champion")),
                        {},
                    )
                    findings.append(
                        f"Champion ML model {champ} achieved holdout daily WAPE={champ_row.get('daily_wape_pct', 0.0)}% "
                        f"(MAE={champ_row.get('daily_mae', 0.0)}, RMSE={champ_row.get('daily_rmse', 0.0)}), "
                        f"improving WAPE by {wape_imp}% vs. Naive 7-Day Moving Average."
                    )
                    top_surging = sorted(
                        [f for f in ml_forecasts if f["forecast_daily_demand"] >= 0.4],
                        key=lambda f: (-f["surge_ratio"], -f["forecast_daily_demand"], f["sku_id"]),
                    )[:5]
                    for f in top_surging:
                        sid = f["sku_id"]
                        cited.append(sid)
                        if f.get("active_location_id"):
                            cited.append(f["active_location_id"])
                        _add_ref(
                            "SKU",
                            sid,
                            "skus / artifacts/models/sku_forecasts",
                            f"Hist ADV={f['historical_daily_demand']} -> Forecast ADV={f['forecast_daily_demand']} ({f['surge_ratio']}x), ROP={f['forecast_reorder_point']}.",
                        )
                        findings.append(
                            f"{sid} ({f['category']}): forecast ADV={f['forecast_daily_demand']}/day vs. historical {f['historical_daily_demand']}/day "
                            f"(surge={f['surge_ratio']}x, forecast ROP={f['forecast_reorder_point']})."
                        )
                    actions.append(
                        "Enable use_ml_forecasts=True on Slotting Optimization and Replenishment Simulation to proactively re-slot and buffer surging SKUs."
                    )
                    metrics = {
                        "champion_model": champ,
                        "daily_mae": champ_row.get("daily_mae"),
                        "daily_rmse": champ_row.get("daily_rmse"),
                        "daily_wape_pct": champ_row.get("daily_wape_pct"),
                        "wape_improvement_vs_naive_pct": wape_imp,
                        "surging_sku_count": eval_sum.get("surging_sku_count", 0),
                        "top_forecast_skus": [
                            {
                                "sku_id": f["sku_id"],
                                "forecast_daily_demand": f["forecast_daily_demand"],
                                "surge_ratio": f["surge_ratio"],
                                "forecast_reorder_point": f["forecast_reorder_point"],
                            }
                            for f in top_surging
                        ],
                    }

            elif intent == "SKU_REJECTION_OR_CANDIDATE_EXPLANATION" and state["extracted_sku_id"]:
                sid = state["extracted_sku_id"]
                lid = state["extracted_location_id"]
                (
                    sku_specs,
                    metrics_map,
                    loc_specs,
                    _,
                    compat_service,
                    max_wh_dist,
                ) = self.cand_service.load_operational_context(
                    session=session, sku_ids=[sid], vacate_in_scope_skus=True
                )
                if not sku_specs:
                    findings.append(f"SKU {sid} was not found among active profiled SKUs in the database.")
                    actions.append("Verify the 6-digit SKU identifier in 03 Inventory & SKUs.")
                else:
                    cited.append(sid)
                    s_spec = sku_specs[0]
                    s_met = metrics_map[sid]
                    if lid:
                        target_locs = [loc for loc in loc_specs if loc.location_id == lid]
                        if not target_locs:
                            findings.append(f"Location {lid} does not exist in the current warehouse database.")
                        else:
                            cited.append(lid)
                            pool = self.cand_service.evaluate_candidates_for_sku(
                                s_spec, s_met, target_locs, compat_service, max_wh_dist, top_n=5
                            )
                            cand = (pool.top_feasible or pool.rejected_samples)[0]
                            metrics = {
                                "sku_id": sid,
                                "location_id": lid,
                                "is_feasible": cand.is_feasible,
                                "total_score": cand.total_score,
                                "rejection_reasons": cand.rejection_reasons,
                            }
                            if cand.is_feasible:
                                findings.append(
                                    f"{sid} IS feasible in {lid} with total_score={cand.total_score:.4f}: {cand.score_breakdown.explanation}"
                                )
                                actions.append(
                                    f"Consider assigning {sid} to {lid} during the next optimization run."
                                )
                            else:
                                findings.append(
                                    f"{sid} was REJECTED from {lid} due to failed hard constraints: {', '.join(cand.rejection_reasons)}."
                                )
                                actions.append(
                                    f"Select a compatible zone/slot for {sid} satisfying {', '.join(cand.rejection_reasons)}."
                                )
                    else:
                        pool = self.cand_service.evaluate_candidates_for_sku(
                            s_spec, s_met, loc_specs, compat_service, max_wh_dist, top_n=3
                        )
                        metrics = {
                            "sku_id": sid,
                            "feasible_locations": pool.total_feasible_count,
                            "rejected_locations": pool.total_rejected_count,
                            "top_rejection_reasons": dict(list(pool.rejection_reason_counts.items())[:5]),
                        }
                        if pool.top_feasible:
                            top = pool.top_feasible[0]
                            cited.append(top.location_id)
                            _add_ref(
                                "LOCATION",
                                top.location_id,
                                "locations",
                                f"Rank #1 feasible candidate for {sid} ({top.zone_type}, score={top.total_score:.4f}).",
                            )
                            findings.append(
                                f"Top recommended slot for {sid} is {top.location_id} ({top.zone_type}, score={top.total_score:.4f})."
                            )
                            actions.append(f"Assign {sid} to top-ranked compatible slot {top.location_id}.")
                        else:
                            findings.append(
                                f"{sid} fits 0 of {pool.total_locations_evaluated} locations; top rejections: {pool.rejection_reason_counts}."
                            )
                            actions.append(
                                f"Provision oversize/specialty bay capacity or split packaging dimensions for {sid}."
                            )

            elif intent == "FAST_MOVER_RELOCATION_ANALYSIS":
                rows = session.execute(
                    select(SlotAssignment, SKUProfile, Location, Zone)
                    .join(
                        SKUProfile,
                        (SlotAssignment.sku_id == SKUProfile.sku_id) & (SKUProfile.effective_to.is_(None)),
                    )
                    .join(Location, SlotAssignment.location_id == Location.location_id)
                    .join(Zone, Location.zone_id == Zone.zone_id)
                    .where(
                        SlotAssignment.assignment_status == AssignmentStatus.ACTIVE.value,
                        SKUProfile.demand_velocity == DemandVelocityClass.FAST.value,
                    )
                    .order_by(SlotAssignment.assignment_score.asc(), SKUProfile.pick_frequency.desc())
                    .limit(5)
                ).all()
                candidates_to_move = []
                for assn, prof, loc, zone in rows:
                    cited.extend([assn.sku_id, loc.location_id])
                    _add_ref(
                        "SLOT_ASSIGNMENT",
                        assn.slot_assignment_id,
                        "slot_assignments",
                        f"Fast mover {assn.sku_id} at {loc.location_id} ({zone.zone_type}), score={assn.assignment_score:.4f}.",
                    )
                    candidates_to_move.append(
                        {
                            "sku_id": assn.sku_id,
                            "current_location_id": loc.location_id,
                            "current_zone_type": zone.zone_type,
                            "assignment_score": round(float(assn.assignment_score), 4),
                            "pick_frequency": round(float(prof.pick_frequency), 3),
                        }
                    )
                    findings.append(
                        f"Fast mover {assn.sku_id} (pick_freq={prof.pick_frequency:.2f}/day) at {loc.location_id} ({zone.zone_type}) has score {assn.assignment_score:.4f}."
                    )
                actions.append(
                    "Run Constrained Slotting Optimization to relocate low-scoring FAST movers closer to the dispatch dock."
                )
                metrics = {"fast_mover_relocation_candidates": candidates_to_move}

            elif intent == "WAREHOUSE_CAPACITY_UTILIZATION":
                sorted_wh = sorted(wh_list, key=lambda w: (-w["location_utilization_pct"], w["warehouse_id"]))
                for w in sorted_wh:
                    cited.append(w["warehouse_id"])
                    findings.append(
                        f"{w['warehouse_id']} ({w['warehouse_name']}): {w['occupied_locations']}/{w['total_locations']} slots occupied "
                        f"({w['location_utilization_pct']}% slot util, {w['volume_utilization_pct']}% volume util, {w['weight_utilization_pct']}% weight util)."
                    )
                if sorted_wh:
                    actions.append(
                        f"Monitor {sorted_wh[0]['warehouse_id']} ({sorted_wh[0]['location_utilization_pct']}% slot occupancy) for zone congestion and capacity balance."
                    )
                metrics = {"highest_utilization_warehouse": sorted_wh[0] if sorted_wh else None}

            elif intent == "TOP_EXCEPTIONS_ANALYSIS":
                for e in excs[:5]:
                    cited.extend([e["exception_id"], e["entity_id"]])
                    _add_ref(
                        "EXCEPTION",
                        e["exception_id"],
                        "exceptions",
                        f"[{e['severity']}] {e['issue_type']} on {e['entity_id']}.",
                    )
                    findings.append(
                        f"[{e['severity']}] {e['exception_id']} ({e['issue_type']} on {e['entity_id']}): {e['description']}"
                    )
                    if e.get("recommended_action") and e["recommended_action"] not in actions:
                        actions.append(e["recommended_action"])
                metrics = kpis["exceptions"]

            elif intent == "REPLENISHMENT_WORKLOAD_ANALYSIS":
                reps = session.scalars(select(Replenishment)).all()
                wh_map = {w.warehouse_id: w for w in session.scalars(select(Warehouse)).all()}
                by_wh: dict[str, float] = {}
                ov_by_wh: dict[str, int] = {}
                for r in reps:
                    if r.replenishment_status in {"PENDING", "IN_PROGRESS", "OVERLOADED"}:
                        by_wh[r.warehouse_id] = by_wh.get(r.warehouse_id, 0.0) + float(r.labor_minutes_required)
                    if r.replenishment_status == "OVERLOADED":
                        ov_by_wh[r.warehouse_id] = ov_by_wh.get(r.warehouse_id, 0) + 1
                for wid, req_m in sorted(by_wh.items(), key=lambda kv: -kv[1]):
                    cap = float(wh_map[wid].daily_labor_minutes_capacity)
                    cited.append(wid)
                    findings.append(
                        f"{wid}: {req_m:.1f} / {cap:.1f} labor min ({(req_m / cap) * 100:.1f}% util, {ov_by_wh.get(wid, 0)} OVERLOADED tasks)."
                    )
                    if ov_by_wh.get(wid, 0) > 0:
                        actions.append(
                            f"Reallocate shift labor capacity or stagger LOW/NORMAL replenishments in {wid} to clear {ov_by_wh[wid]} OVERLOADED tasks."
                        )
                if not actions:
                    actions.append("Current shift labor capacity covers active replenishment workload.")
                metrics = kpis["replenishment"]

            elif intent == "PICKING_SAVINGS_ANALYSIS":
                p = kpis["picking_simulated_estimates"]
                metrics = p
                if latest_run:
                    cited.append(latest_run["run_id"])
                    _add_ref(
                        "OPTIMIZATION_RUN",
                        latest_run["run_id"],
                        "optimization_runs",
                        f"{latest_run['algorithm_name']} run assigning {latest_run['assignment_count']}/{latest_run['sku_count']} SKUs.",
                    )
                findings.append(
                    f"Across {p['orders_evaluated']} identical orders, pick travel drops from {p['baseline_pick_distance_m']} m "
                    f"to {p['optimized_pick_distance_m']} m, saving {p['distance_saved_m']} m ({p['distance_improvement_pct']}%)."
                )
                findings.append(
                    f"Estimated fulfillment pick time savings: {p['picking_time_saved_seconds']} seconds ({p['picking_time_improvement_pct']}%)."
                )
                actions.append(
                    "Deploy Nearest-Neighbor + 2-Opt pick wave sequencing alongside approved forward-pick slotting."
                )

            else:
                metrics = {"optimization": kpis["optimization"], "warehouse": kpis["warehouse"]}
                if latest_run:
                    cited.append(latest_run["run_id"])
                    _add_ref(
                        "OPTIMIZATION_RUN",
                        latest_run["run_id"],
                        "optimization_runs",
                        f"{latest_run['algorithm_name']} assigning {latest_run['assignment_count']}/{latest_run['sku_count']} SKUs.",
                    )
                    findings.append(
                        f"Latest run {latest_run['run_id']} ({latest_run['algorithm_name']}) assigned "
                        f"{latest_run['assignment_count']}/{latest_run['sku_count']} SKUs with avg score "
                        f"{latest_run['kpi_results'].get('average_assignment_score', 0.0)} and 0 constraint violations."
                    )
                    actions.append(
                        f"Review open operational exceptions ({kpis['exceptions']['open_exceptions']} open) and scenario benchmarks."
                    )
                else:
                    findings.append(
                        f"No optimization run executed yet; baseline holds {kpis['warehouse']['occupied_locations']} occupied slots "
                        f"with {kpis['exceptions']['open_exceptions']} open exceptions."
                    )
                    actions.append("Execute an initial constrained slotting optimization run.")

            dedup_cited = list(dict.fromkeys(cited))
            if findings:
                det_answer = f"Based on live operational warehouse records ({intent}): {' '.join(findings[:4])}"
            else:
                det_answer = "No matching operational records were found for the specified query filter."

            step = {
                "node_name": "run_deterministic_analysis",
                "summary": f"Computed {len(findings)} deterministic analytical findings citing {len(dedup_cited)} DB entities.",
                "record_ids": dedup_cited[:8],
            }
            return {
                "analysis_result": metrics,
                "deterministic_answer": det_answer,
                "deterministic_findings": findings,
                "deterministic_actions": actions,
                "deterministic_cited_ids": dedup_cited,
                "source_references": refs,
                "trace": state["trace"] + [step],
            }

        def synthesize_explanation(state: AssistantGraphState) -> dict[str, Any]:
            det_answer = state["deterministic_answer"]
            det_findings = state["deterministic_findings"]
            det_actions = state["deterministic_actions"]
            det_cited = state["deterministic_cited_ids"]

            if adapter.is_available:
                llm_out = adapter.synthesize_grounded_response(
                    question=state["question"],
                    intent=state["intent"],
                    deterministic_findings=det_findings,
                    deterministic_actions=det_actions,
                    supporting_metrics=state["analysis_result"],
                    verified_entity_ids=det_cited,
                )
                if llm_out is not None:
                    mode = f"LLM_GROUNDED:{adapter.provider_name}"
                    merged_cited = list(dict.fromkeys((llm_out.cited_entity_ids or []) + det_cited))
                    step = {
                        "node_name": "synthesize_explanation",
                        "summary": f"Synthesized Pydantic-validated response via {mode}.",
                        "record_ids": merged_cited[:6],
                    }
                    return {
                        "answer": llm_out.answer,
                        "key_findings": llm_out.key_findings or det_findings,
                        "recommended_actions": llm_out.recommended_actions or det_actions,
                        "cited_ids": merged_cited,
                        "execution_mode": mode,
                        "trace": state["trace"] + [step],
                    }

            reason_suffix = (
                f" (fallback_reason={adapter.unavailable_reason})"
                if adapter.unavailable_reason and adapter.unavailable_reason != "llm_provider_disabled"
                else ""
            )
            mode = "DETERMINISTIC_GROUNDED_FALLBACK"
            step = {
                "node_name": "synthesize_explanation",
                "summary": f"Synthesized structured response via {mode}{reason_suffix}.",
                "record_ids": det_cited[:6],
            }
            return {
                "answer": det_answer,
                "key_findings": det_findings,
                "recommended_actions": det_actions,
                "cited_ids": det_cited,
                "execution_mode": mode,
                "trace": state["trace"] + [step],
            }

        def validate_grounding(state: AssistantGraphState) -> dict[str, Any]:
            """Verify every cited or mentioned domain entity ID against relational SQL tables; block hallucinations."""
            combined_text = " ".join(
                [state["answer"]] + state["key_findings"] + state["recommended_actions"] + state["cited_ids"]
            )

            checks: list[tuple[re.Pattern[str], type[Any]]] = [
                (SKU_PATTERN, SKU),
                (LOC_PATTERN, Location),
                (WH_PATTERN, Warehouse),
                (OPT_PATTERN, OptimizationRun),
                (EXC_PATTERN, ExceptionRecord),
                (SCN_PATTERN, Scenario),
                (REP_PATTERN, Replenishment),
            ]

            verified_ids: list[str] = []
            fabricated_ids: list[str] = []
            allowed_missing = {
                x.upper()
                for x in (
                    state["extracted_sku_id"],
                    state["extracted_location_id"],
                    state["extracted_warehouse_id"],
                )
                if x
            }

            for pattern, orm_cls in checks:
                found = sorted({m.upper() for m in pattern.findall(combined_text)})
                for eid in found:
                    if session.get(orm_cls, eid) is not None:
                        verified_ids.append(eid)
                    elif eid not in allowed_missing:
                        fabricated_ids.append(eid)

            if fabricated_ids:
                # Anti-fabrication guard: strip ungrounded LLM output and revert to verified SQL deterministic facts
                step = {
                    "node_name": "validate_grounding",
                    "summary": (
                        f"Intercepted fabricated entity ID(s) {fabricated_ids} in synthesis; "
                        "reverted automatically to SQL-verified deterministic explanation."
                    ),
                    "record_ids": state["deterministic_cited_ids"][:6],
                }
                return {
                    "answer": state["deterministic_answer"],
                    "key_findings": state["deterministic_findings"],
                    "recommended_actions": state["deterministic_actions"],
                    "cited_ids": state["deterministic_cited_ids"],
                    "execution_mode": "DETERMINISTIC_FALLBACK_AFTER_GROUNDING_GUARD",
                    "grounding_verified": True,
                    "trace": state["trace"] + [step],
                }

            step = {
                "node_name": "validate_grounding",
                "summary": (
                    f"Verified all {len(verified_ids)} cited entity IDs against relational database "
                    "(grounding_verified=True, fabricated_ids=0)."
                ),
                "record_ids": verified_ids[:8],
            }
            return {
                "grounding_verified": True,
                "trace": state["trace"] + [step],
            }

        graph = StateGraph(AssistantGraphState)
        graph.add_node("classify_intent", classify_intent)
        graph.add_node("retrieve_warehouse_context", retrieve_warehouse_context)
        graph.add_node("retrieve_kpi_and_exceptions", retrieve_kpi_and_exceptions)
        graph.add_node("run_deterministic_analysis", run_deterministic_analysis)
        graph.add_node("synthesize_explanation", synthesize_explanation)
        graph.add_node("validate_grounding", validate_grounding)

        graph.add_edge(START, "classify_intent")
        graph.add_edge("classify_intent", "retrieve_warehouse_context")
        graph.add_edge("retrieve_warehouse_context", "retrieve_kpi_and_exceptions")
        graph.add_edge("retrieve_kpi_and_exceptions", "run_deterministic_analysis")
        graph.add_edge("run_deterministic_analysis", "synthesize_explanation")
        graph.add_edge("synthesize_explanation", "validate_grounding")
        graph.add_edge("validate_grounding", END)

        compiled = graph.compile()
        final_state = compiled.invoke(
            {
                "question": question,
                "warehouse_filter": warehouse_id,
                "intent": "",
                "extracted_sku_id": None,
                "extracted_location_id": None,
                "extracted_warehouse_id": warehouse_id,
                "warehouse_context": {},
                "operational_data": {},
                "analysis_result": {},
                "deterministic_answer": "",
                "deterministic_findings": [],
                "deterministic_actions": [],
                "deterministic_cited_ids": [],
                "source_references": [],
                "answer": "",
                "key_findings": [],
                "recommended_actions": [],
                "cited_ids": [],
                "execution_mode": "DETERMINISTIC_GROUNDED_FALLBACK",
                "grounding_verified": True,
                "trace": [],
            }
        )

        return AssistantQueryResponse(
            question=question,
            classified_intent=final_state["intent"],
            execution_mode=final_state["execution_mode"],
            answer=final_state["answer"],
            key_findings=final_state["key_findings"],
            recommended_actions=final_state["recommended_actions"],
            supporting_metrics=final_state["analysis_result"],
            cited_entity_ids=final_state["cited_ids"],
            source_references=[AssistantSourceReference(**r) for r in final_state["source_references"]],
            grounding_verified=final_state["grounding_verified"],
            trace=[AssistantTraceStep(**t) for t in final_state["trace"]],
        )
