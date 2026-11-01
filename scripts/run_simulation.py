"""End-to-end Phase 3 & Phase 4 slotting optimization, exception detection, and simulation runner."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import func, select  # noqa: E402

from backend.app.analytics.kpi_service import AnalyticsKPIService  # noqa: E402
from backend.app.config import ARTIFACTS_DIR, DATA_DIR, load_domain_config  # noqa: E402
from backend.app.database import get_engine, get_session_factory, session_scope  # noqa: E402
from backend.app.models import AssignmentReason, AuditEvent, ExceptionRecord, SlotAssignment  # noqa: E402
from backend.app.models.enums import AssignmentStatus  # noqa: E402
from backend.app.optimization.solver import SlottingOptimizerService  # noqa: E402
from backend.app.services.data_loader import DatabaseLoader  # noqa: E402
from backend.app.services.exception_engine import ExceptionDetectionService  # noqa: E402
from backend.app.simulation.picking_simulator import PickingSimulationService  # noqa: E402
from backend.app.simulation.replenishment_simulator import ReplenishmentSimulationService  # noqa: E402
from backend.app.simulation.scenario_service import ScenarioComparisonService  # noqa: E402


def run_end_to_end_simulation(
    profile: str = "small",
    reload_db: bool = True,
    strategy: str = "auto",
    max_orders: int = 500,
) -> dict[str, Any]:
    """Execute the complete Phase 3 & Phase 4 warehouse decision pipeline on the target profile."""
    t0 = time.perf_counter()
    config = load_domain_config(profile=profile)

    engine = get_engine()
    session_factory = get_session_factory()

    load_summary: dict[str, Any] | None = None
    if reload_db:
        loader = DatabaseLoader(engine=engine)
        load_summary = loader.load_profile_from_disk(profile=profile, data_root=DATA_DIR, reset_existing=True)

    exc_service = ExceptionDetectionService(config=config)
    opt_service = SlottingOptimizerService(config=config)
    pick_service = PickingSimulationService(config=config)
    rep_service = ReplenishmentSimulationService(config=config)
    scenario_service = ScenarioComparisonService(config=config)
    kpi_service = AnalyticsKPIService(config=config)

    with session_scope(session_factory) as session:
        # 1. Baseline Exception Scan & Ground-Truth Precision/Recall/F1 Benchmark
        baseline_exc_scan = exc_service.run_detection(
            session=session,
            optimization_run_id=None,
            auto_resolve_remediated=False,
            actor="SIMULATION_RUNNER",
        )
        gt_eval = kpi_service.evaluate_exception_detection_against_ground_truth(
            session=session,
            profile=profile,
            data_root=DATA_DIR,
        )
        baseline_kpis = kpi_service.get_overview_kpis(
            session=session,
            sample_picking_orders=max_orders,
        )

        # Capture baseline active locations per (warehouse_id, sku_id) before optimization
        baseline_active_rows = session.scalars(
            select(SlotAssignment).where(SlotAssignment.assignment_status == AssignmentStatus.ACTIVE.value)
        ).all()
        baseline_loc_by_wh_sku = {(r.warehouse_id, r.sku_id): r.location_id for r in baseline_active_rows}
        baseline_total_assignment_rows = len(baseline_active_rows)

        # 2. Run Constrained Slotting Optimization in two-step PROPOSED -> ACTIVE governance flow
        opt_summary = opt_service.run_optimization(
            session=session,
            warehouse_id=None,
            zone_id=None,
            strategy=strategy,
            preserve_warehouse_affinity=True,
            apply_immediately=False,
            actor="SIMULATION_RUNNER",
        )

        proposed_count_before_approval = int(
            session.scalar(
                select(func.count())
                .select_from(SlotAssignment)
                .where(
                    SlotAssignment.optimization_run_id == opt_summary.run_id,
                    SlotAssignment.assignment_status == AssignmentStatus.PROPOSED.value,
                )
            )
            or 0
        )

        approval_summary = opt_service.approve_assignments(
            session=session,
            run_id=opt_summary.run_id,
            actor="WAREHOUSE_MANAGER",
        )

        # Verify persisted assignment lifecycle states and physical SKU relocations
        status_rows = session.execute(
            select(SlotAssignment.assignment_status, func.count()).group_by(SlotAssignment.assignment_status)
        ).all()
        assignment_status_counts = {str(r[0]): int(r[1]) for r in status_rows}

        new_active_rows = session.scalars(
            select(SlotAssignment).where(SlotAssignment.assignment_status == AssignmentStatus.ACTIVE.value)
        ).all()
        moved_sku_count = sum(
            1 for r in new_active_rows if baseline_loc_by_wh_sku.get((r.warehouse_id, r.sku_id)) != r.location_id
        )

        # 3. Post-Optimization Exception Re-Scan (auto-resolving remediated slotting violations)
        post_opt_exc_scan = exc_service.run_detection(
            session=session,
            optimization_run_id=opt_summary.run_id,
            auto_resolve_remediated=True,
            actor="SIMULATION_RUNNER",
        )

        # 4. Picking Path Optimization & Slotting Comparison on Identical Orders
        picking_comparison = pick_service.compare_slotting_layouts(
            session=session,
            optimization_run_id=opt_summary.run_id,
            max_orders=max_orders,
        )

        # 5. Replenishment Workload Simulation (Baseline vs. Optimized Slotting)
        baseline_replenishment = rep_service.simulate_replenishment(
            session=session,
            use_baseline_assignments=True,
            persist_events=False,
        )
        optimized_replenishment = rep_service.simulate_replenishment(
            session=session,
            optimization_run_id=opt_summary.run_id,
            persist_events=False,
        )

        # 6. Scenario Comparison & Post-Optimization KPIs
        scenario = scenario_service.create_and_evaluate_scenario(
            session=session,
            optimization_run_id=opt_summary.run_id,
            scenario_name=f"Phase 3-4 End-to-End Slotting & Routing ({profile})",
            description="Automated comparison of baseline warehouse slotting vs. constrained optimization.",
            max_orders=max_orders,
            actor="SIMULATION_RUNNER",
        )
        post_opt_kpis = kpi_service.get_overview_kpis(
            session=session,
            sample_picking_orders=max_orders,
        )

        reason_total = int(session.scalar(select(func.count()).select_from(AssignmentReason)) or 0)
        rejected_reason_total = int(
            session.scalar(
                select(func.count()).select_from(AssignmentReason).where(AssignmentReason.is_feasible.is_(False))
            )
            or 0
        )
        audit_event_total = int(session.scalar(select(func.count()).select_from(AuditEvent)) or 0)
        exception_record_total = int(session.scalar(select(func.count()).select_from(ExceptionRecord)) or 0)

        scenario_payload = {
            "scenario_id": scenario.scenario_id,
            "scenario_name": scenario.scenario_name,
            "optimization_run_id": scenario.optimization_run_id,
            "comparison_delta": scenario.comparison_delta_json,
        }

    total_elapsed_s = round(time.perf_counter() - t0, 3)

    report: dict[str, Any] = {
        "profile": profile,
        "seed": config.project.seed,
        "total_elapsed_seconds": total_elapsed_s,
        "database_load": load_summary,
        "baseline_exception_detection": baseline_exc_scan,
        "ground_truth_evaluation": gt_eval,
        "optimization_run": {
            "run_id": opt_summary.run_id,
            "algorithm_name": opt_summary.algorithm_name,
            "sku_count": opt_summary.sku_count,
            "assigned_count": opt_summary.assigned_count,
            "unassigned_count": opt_summary.unassigned_count,
            "unassigned_skus": opt_summary.unassigned_skus,
            "candidate_count": opt_summary.candidate_count,
            "rejected_candidate_count": opt_summary.rejected_candidate_count,
            "average_assignment_score": opt_summary.average_assignment_score,
            "execution_duration_ms": opt_summary.execution_duration_ms,
            "proposed_count_before_approval": proposed_count_before_approval,
            "approval": approval_summary,
            "baseline_assignment_rows_preserved": baseline_total_assignment_rows,
            "assignment_status_counts": assignment_status_counts,
            "moved_sku_count": moved_sku_count,
        },
        "post_optimization_exception_scan": post_opt_exc_scan,
        "picking_comparison": {k: v for k, v in picking_comparison.items() if k != "sample_orders"},
        "replenishment_comparison": {
            "baseline": {k: v for k, v in baseline_replenishment.items() if k != "tasks"},
            "optimized": {k: v for k, v in optimized_replenishment.items() if k != "tasks"},
        },
        "scenario_comparison": scenario_payload,
        "kpis": {
            "baseline": baseline_kpis,
            "post_optimization": post_opt_kpis,
        },
        "auditability": {
            "persisted_assignment_reasons": reason_total,
            "persisted_rejected_candidate_reasons": rejected_reason_total,
            "persisted_exception_records": exception_record_total,
            "persisted_audit_events": audit_event_total,
        },
    }

    reports_dir = ARTIFACTS_DIR / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"{profile}_phase3_4_simulation_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run SlotWise AI Phase 3 & 4 end-to-end simulation.")
    parser.add_argument("--profile", type=str, default="small", choices=["small", "medium", "full"])
    parser.add_argument("--no-reload-db", action="store_true", help="Do not reload CSV dataset before running.")
    parser.add_argument(
        "--strategy",
        type=str,
        default="auto",
        choices=["auto", "ortools_cpsat", "greedy"],
        help="Optimization solver strategy.",
    )
    parser.add_argument("--max-orders", type=int, default=500, help="Max orders to compare in pick simulation.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = run_end_to_end_simulation(
        profile=args.profile,
        reload_db=not args.no_reload_db,
        strategy=args.strategy,
        max_orders=args.max_orders,
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
