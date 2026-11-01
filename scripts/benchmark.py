"""Reproducible multi-scale benchmarking script for SlotWise AI.

Measures wall-clock latency, peak memory (tracemalloc), and operational throughput across:
1. Synthetic master data generation
2. 18-rule constraint & integrity validation
3. Database ingestion
4. Constrained slotting optimization (OR-Tools / Greedy)
5. Routing & picking path simulation
6. Replenishment workload simulation
7. ML SKU demand forecaster training & evaluation

Saves structured benchmark results to `artifacts/benchmarks/benchmark_results.json`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from backend.app.config import DATA_DIR, load_domain_config  # noqa: E402
from backend.app.database import create_db_engine  # noqa: E402
from backend.app.ml.forecaster import DemandForecastingService  # noqa: E402
from backend.app.optimization.solver import SlottingOptimizerService  # noqa: E402
from backend.app.services.data_generator import SyntheticDataGenerator  # noqa: E402
from backend.app.services.data_loader import DatabaseLoader  # noqa: E402
from backend.app.simulation.picking_simulator import PickingSimulationService  # noqa: E402
from backend.app.simulation.replenishment_simulator import ReplenishmentSimulationService  # noqa: E402
from backend.app.validators.dataset_validator import DatasetValidator  # noqa: E402


def benchmark_step(name: str, fn: Any, *args: Any, **kwargs: Any) -> tuple[Any, dict[str, Any]]:
    """Execute a function while recording elapsed wall-clock seconds and peak memory MB."""
    tracemalloc.start()
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed = round(time.perf_counter() - t0, 4)
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    peak_mb = round(peak_bytes / (1024 * 1024), 2)
    meta = {
        "step_name": name,
        "elapsed_seconds": elapsed,
        "peak_memory_mb": peak_mb,
    }
    return result, meta


def run_benchmark_for_profile(
    profile: str = "small",
    max_orders: int = 500,
    include_ml: bool = True,
    artifacts_dir: Path | None = None,
) -> dict[str, Any]:
    """Run a standardized end-to-end benchmark on the requested dataset profile."""
    config = load_domain_config(profile=profile)
    artifacts_root = artifacts_dir or (PROJECT_ROOT / "artifacts")
    benchmarks_dir = artifacts_root / "benchmarks"
    benchmarks_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n>>> Running Benchmark for Profile: {profile.upper()} (Seed={config.project.seed})")

    # 1. Synthetic Data Generation
    generator = SyntheticDataGenerator(config=config, profile=profile)

    def _gen_and_export() -> int:
        bundle = generator.generate()
        generator.export_bundle(bundle, output_root=DATA_DIR)
        return sum(len(df) for df in bundle.tables.values())

    total_records, gen_meta = benchmark_step(
        "synthetic_data_generation",
        _gen_and_export,
    )
    gen_meta["records_generated"] = total_records
    gen_meta["throughput_records_per_sec"] = (
        round(total_records / gen_meta["elapsed_seconds"], 2) if gen_meta["elapsed_seconds"] > 0 else 0.0
    )
    print(
        f"  [1/7] Data Generation       : {gen_meta['elapsed_seconds']}s | {gen_meta['peak_memory_mb']} MB peak | {total_records:,} rows ({gen_meta['throughput_records_per_sec']:,.0f} rows/s)"
    )

    # 2. 18-Rule Constraint & Integrity Validation
    validator = DatasetValidator(config=config, profile=profile)

    def _run_val() -> dict[str, Any]:
        tables, gt_df = validator.load_tables_from_disk(profile=profile, data_root=DATA_DIR)
        return validator.validate(tables=tables, ground_truth_df=gt_df)

    val_report, val_meta = benchmark_step(
        "data_validation",
        _run_val,
    )
    val_meta["rules_evaluated"] = 18
    val_meta["validation_status"] = "PASSED" if val_report.get("is_valid") else "FAILED"
    print(
        f"  [2/7] 18-Rule Validation    : {val_meta['elapsed_seconds']}s | {val_meta['peak_memory_mb']} MB peak | Status: {val_meta['validation_status']}"
    )

    # 3. Database Ingestion
    bench_db = DATA_DIR / f"slotwise_{profile}_bench.db"
    if bench_db.exists():
        bench_db.unlink(missing_ok=True)
    engine = create_db_engine(f"sqlite:///{bench_db.as_posix()}")
    loader = DatabaseLoader(engine=engine, batch_size=config.dataset.batch_size)
    load_res, load_meta = benchmark_step(
        "database_ingestion",
        loader.load_profile_from_disk,
        profile=profile,
        data_root=DATA_DIR,
        reset_existing=True,
    )
    loaded_counts = load_res.get("inserted_counts", {}) if isinstance(load_res, dict) else load_res.loaded_records
    load_meta["records_loaded"] = sum(loaded_counts.values())
    load_meta["throughput_records_per_sec"] = (
        round(load_meta["records_loaded"] / load_meta["elapsed_seconds"], 2)
        if load_meta["elapsed_seconds"] > 0
        else 0.0
    )
    print(
        f"  [3/7] Database Ingestion    : {load_meta['elapsed_seconds']}s | {load_meta['peak_memory_mb']} MB peak | {load_meta['records_loaded']:,} records loaded"
    )

    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )

    # 4. Constrained Slotting Optimization
    opt_service = SlottingOptimizerService(config=config)
    with session_factory() as session:
        opt_summary, opt_meta = benchmark_step(
            "slotting_optimization",
            opt_service.run_optimization,
            session=session,
            warehouse_id=None,
            zone_id=None,
            strategy="auto",
            preserve_warehouse_affinity=True,
            apply_immediately=True,
            actor="BENCHMARK_RUNNER",
        )
        session.commit()
    opt_meta["sku_count"] = opt_summary.sku_count
    opt_meta["candidate_count"] = opt_summary.candidate_count
    opt_meta["assigned_count"] = opt_summary.assigned_count
    opt_meta["success_rate_pct"] = round((opt_summary.assigned_count / max(1, opt_summary.sku_count)) * 100.0, 2)
    opt_meta["solver_duration_ms"] = opt_summary.execution_duration_ms
    print(
        f"  [4/7] Slotting Optimization : {opt_meta['elapsed_seconds']}s | {opt_meta['peak_memory_mb']} MB peak | {opt_summary.assigned_count}/{opt_summary.sku_count} SKUs assigned ({opt_meta['success_rate_pct']}%) | {opt_summary.candidate_count:,} candidates"
    )

    # 5. Picking Path Simulation
    pick_service = PickingSimulationService(config=config)
    with session_factory() as session:
        pick_cmp, pick_meta = benchmark_step(
            "picking_simulation",
            pick_service.compare_slotting_layouts,
            session=session,
            optimization_run_id=opt_summary.run_id,
            max_orders=max_orders,
        )
    pick_meta["orders_evaluated"] = pick_cmp["orders_evaluated"]
    pick_meta["distance_saved_m"] = pick_cmp["distance_saved_m"]
    pick_meta["distance_improvement_pct"] = pick_cmp["percentage_improvement"]
    pick_meta["time_improvement_pct"] = pick_cmp["time_improvement_pct"]
    pick_meta["throughput_orders_per_sec"] = (
        round(pick_cmp["orders_evaluated"] / pick_meta["elapsed_seconds"], 2)
        if pick_meta["elapsed_seconds"] > 0
        else 0.0
    )
    print(
        f"  [5/7] Picking Simulation    : {pick_meta['elapsed_seconds']}s | {pick_meta['peak_memory_mb']} MB peak | {pick_cmp['orders_evaluated']} orders ({pick_cmp['percentage_improvement']:+.2f}% dist, {pick_cmp['time_improvement_pct']:+.2f}% time)"
    )

    # 6. Replenishment Workload Simulation
    rep_service = ReplenishmentSimulationService(config=config)
    with session_factory() as session:
        rep_cmp, rep_meta = benchmark_step(
            "replenishment_simulation",
            rep_service.simulate_replenishment,
            session=session,
            optimization_run_id=opt_summary.run_id,
            persist_events=False,
        )
    rep_meta["task_count"] = rep_cmp["total_requests"]
    rep_meta["total_labor_minutes"] = rep_cmp["total_labor_minutes_required"]
    rep_meta["overloaded_tasks"] = rep_cmp["overloaded_task_count"]
    print(
        f"  [6/7] Replenishment Sim     : {rep_meta['elapsed_seconds']}s | {rep_meta['peak_memory_mb']} MB peak | {rep_cmp['total_requests']} tasks ({rep_cmp['total_labor_minutes_required']} labor mins)"
    )

    # 7. ML Demand Forecaster Training (Optional / Enabled)
    ml_meta: dict[str, Any] = {"step_name": "ml_forecaster_training", "status": "SKIPPED"}
    if include_ml:
        ml_service = DemandForecastingService(config=config, artifacts_root=artifacts_root)
        with session_factory() as session:
            ml_summary, ml_meta = benchmark_step(
                "ml_forecaster_training",
                ml_service.train_and_evaluate,
                session=session,
                profile=profile,
                forecast_horizon_days=14,
                train_ratio=0.80,
                actor="BENCHMARK_RUNNER",
            )
            session.commit()
        ml_meta["champion_model"] = ml_summary["champion_model_name"]
        ml_meta["baseline_reference"] = ml_summary.get("baseline_reference_model", "NAIVE")
        ml_meta["wape_improvement_pct"] = ml_summary["wape_improvement_vs_naive_pct"]
        ml_meta["active_sku_count"] = ml_summary["split_info"]["active_sku_count"]
        print(
            f"  [7/7] ML Demand Forecaster  : {ml_meta['elapsed_seconds']}s | {ml_meta['peak_memory_mb']} MB peak | Champion: {ml_meta['champion_model']} ({ml_meta['wape_improvement_pct']:+.2f}% WAPE improvement)"
        )

    engine.dispose()
    if bench_db.exists():
        bench_db.unlink(missing_ok=True)

    total_pipeline_time = round(
        gen_meta["elapsed_seconds"]
        + val_meta["elapsed_seconds"]
        + load_meta["elapsed_seconds"]
        + opt_meta["elapsed_seconds"]
        + pick_meta["elapsed_seconds"]
        + rep_meta["elapsed_seconds"]
        + (ml_meta["elapsed_seconds"] if include_ml else 0.0),
        3,
    )

    profile_report = {
        "profile": profile,
        "warehouses": config.dataset.warehouses,
        "locations": config.dataset.locations,
        "skus": config.dataset.skus,
        "order_lines": config.dataset.order_lines,
        "total_pipeline_seconds": total_pipeline_time,
        "benchmarks": {
            "synthetic_data_generation": gen_meta,
            "data_validation": val_meta,
            "database_ingestion": load_meta,
            "slotting_optimization": opt_meta,
            "picking_simulation": pick_meta,
            "replenishment_simulation": rep_meta,
            "ml_forecaster_training": ml_meta,
        },
    }
    return profile_report


def main() -> int:
    parser = argparse.ArgumentParser(description="SlotWise AI Multi-Scale Benchmarking Suite.")
    parser.add_argument(
        "--profiles",
        nargs="+",
        default=["small", "medium"],
        choices=["small", "medium", "full"],
        help="Dataset profile(s) to benchmark.",
    )
    parser.add_argument("--max-orders", type=int, default=500, help="Max orders for picking simulation benchmark.")
    parser.add_argument("--no-ml", action="store_true", help="Skip ML forecasting step in benchmark.")
    args = parser.parse_args()

    results: dict[str, Any] = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "profiles_benchmarked": args.profiles,
        "profiles": {},
    }

    print("=" * 96)
    print("SlotWise AI — End-to-End Multi-Scale Operational Benchmarking Suite")
    print("=" * 96)

    for prof in args.profiles:
        prof_res = run_benchmark_for_profile(
            profile=prof,
            max_orders=args.max_orders,
            include_ml=not args.no_ml,
        )
        results["profiles"][prof] = prof_res

    out_file = PROJECT_ROOT / "artifacts" / "benchmarks" / "benchmark_results.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("\n" + "=" * 96)
    print(f"Benchmark results successfully persisted to: {out_file.as_posix()}")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
