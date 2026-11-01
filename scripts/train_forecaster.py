"""CLI utility to train SKU-level ML demand forecasters, track MLflow runs, and save forecast artifacts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import load_domain_config  # noqa: E402
from backend.app.database import get_session_factory  # noqa: E402
from backend.app.ml.forecaster import DemandForecastingService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Train SlotWise AI chronological SKU demand forecasting models.")
    parser.add_argument("--profile", type=str, default="small", choices=["small", "medium", "full"])
    parser.add_argument("--horizon", type=int, default=14, help="Forward forecast horizon in days.")
    parser.add_argument("--train-ratio", type=float, default=0.80, help="Chronological train split fraction.")
    args = parser.parse_args()

    cfg = load_domain_config(profile=args.profile)
    service = DemandForecastingService(config=cfg)
    session_factory = get_session_factory()

    with session_factory() as session:
        summary = service.train_and_evaluate(
            session=session,
            profile=args.profile,
            forecast_horizon_days=args.horizon,
            train_ratio=args.train_ratio,
            actor="CLI_TRAIN_FORECASTER",
        )
        session.commit()

    split = summary["split_info"]
    print("=" * 88)
    print(f"SlotWise AI — Chronological SKU Demand Forecasting & MLflow Report ({summary['profile'].upper()})")
    print("=" * 88)
    print(
        f"Chronological Split : Train {split['train_start_date']} .. {split['train_end_date']} "
        f"({split['train_rows']:,} rows) | Test {split['test_start_date']} .. {split['test_end_date']} "
        f"({split['test_rows']:,} rows) | Verified={split['chronological_order_verified']}"
    )
    print(f"MLflow Experiment   : {summary['mlflow_experiment_name']} ({summary['mlflow_tracking_uri']})")
    print(f"Saved Model Bundle  : {summary['artifact_joblib_path']}")
    print("-" * 88)
    print(
        f"{'Model Name':<34} | {'Family':<16} | {'Daily MAE':>9} | {'Daily RMSE':>10} | {'Daily WAPE%':>11} | {'Horiz WAPE%':>11}"
    )
    print("-" * 88)
    for row in summary["leaderboard"]:
        star = " *" if row["is_champion"] else "  "
        print(
            f"{(row['model_name'] + star):<34} | {row['model_family']:<16} | "
            f"{row['daily_mae']:>9.4f} | {row['daily_rmse']:>10.4f} | "
            f"{row['daily_wape_pct']:>10.2f}% | {row['horizon_wape_pct']:>10.2f}%"
        )
    print("-" * 88)
    print(
        f"Champion Model      : {summary['champion_model_name']} "
        f"(WAPE Improvement vs. {summary.get('baseline_reference_model', 'Naive Baseline')}: {summary['wape_improvement_vs_naive_pct']:+.2f}%, "
        f"MAE Improvement: {summary['mae_improvement_vs_naive_pct']:+.2f}%)"
    )
    print(
        f"Operational Signals : {summary['surging_sku_count']} surging SKUs | "
        f"{summary['velocity_shift_sku_count']} velocity-tier shifts across {split['active_sku_count']} SKUs"
    )
    print("=" * 88)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
