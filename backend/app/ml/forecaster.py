"""SKU-level chronological demand forecasting, Naive vs. ML evaluation, MLflow tracking, and operational integration.

Design guarantees:
- Strict chronological train/test splits (`max(train_date) < min(test_date)`) with zero future data leakage.
- Compares operational Naive Baselines (Last-Observation & 7-Day Causal Moving Average) against
  Scikit-learn Ridge Regression and Poisson HistGradientBoostingRegressor.
- Computes MAE, RMSE, and WAPE (%) at both daily SKU-day and cumulative SKU-horizon granularities.
- Tracks runs, hyperparameters, and metrics in local MLflow (`artifacts/mlruns`) without external services.
- Saves serialized `.joblib` model bundles and JSON forecast tables under `artifacts/models/`.
- Integrates into Replenishment and Slotting recommendations on an opt-in basis (`use_ml_forecasts=True`)
  so the deterministic core system remains 100% preserved by default.
"""

from __future__ import annotations

import json
import math
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, root_mean_squared_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import ARTIFACTS_DIR, DomainConfig, load_domain_config
from backend.app.models import SKU, Order, OrderLine, SKUProfile, SlotAssignment, Supplier
from backend.app.models.enums import AssignmentStatus, DemandVelocityClass, SKUStatus
from backend.app.services.audit_service import AuditService, utc_now_naive

DAY_INDEX_SCALE: float = 100.0

FEATURE_COLUMNS: list[str] = [
    "lag_1",
    "lag_2",
    "lag_3",
    "lag_7",
    "lag_14",
    "rolling_mean_7",
    "rolling_std_7",
    "rolling_mean_14",
    "rolling_mean_28",
    "rolling_max_14",
    "nonzero_rate_14",
    "ewma_7",
    "momentum_7_28",
    "sku_train_mean",
    "sku_train_std",
    "sku_train_nonzero_rate",
    "sku_train_dow_mean",
    "day_of_week",
    "is_weekend",
    "day_of_month",
    "sin_dow",
    "cos_dow",
    "day_index_norm",
    "log_unit_price",
    "unit_weight_kg",
    "unit_volume_m3",
    "is_fragile",
    "lead_time_days",
    "category_code",
]


def compute_regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    """Compute exact MAE, RMSE, and WAPE (%) with zero-division protection."""
    yt = np.asarray(y_true, dtype=np.float64)
    yp = np.maximum(0.0, np.asarray(y_pred, dtype=np.float64))
    if yt.size == 0:
        return {"mae": 0.0, "rmse": 0.0, "wape_pct": 0.0}

    mae = float(mean_absolute_error(yt, yp))
    rmse = float(root_mean_squared_error(yt, yp))
    denom = float(np.sum(np.abs(yt)))
    wape_pct = float((np.sum(np.abs(yt - yp)) / denom) * 100.0) if denom > 1e-9 else 0.0
    return {
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "wape_pct": round(wape_pct, 4),
    }


class DemandForecastingService:
    """SKU-level demand forecaster with chronological splits, MLflow tracking, and slotting/replenishment hooks."""

    def __init__(
        self,
        config: DomainConfig | None = None,
        artifacts_root: Path | None = None,
    ) -> None:
        self.config = config or load_domain_config()
        self.artifacts_root = artifacts_root or ARTIFACTS_DIR
        self.models_dir = self.artifacts_root / "models"
        self.mlruns_dir = self.artifacts_root / "mlruns"
        self.audit_service = AuditService(reference_year=self.config.dataset.reference_year)

    def _model_path(self, profile: str) -> Path:
        return self.models_dir / f"demand_forecaster_{profile}.joblib"

    def _eval_path(self, profile: str) -> Path:
        return self.models_dir / f"forecast_evaluation_{profile}.json"

    def _forecasts_path(self, profile: str) -> Path:
        return self.models_dir / f"sku_forecasts_{profile}.json"

    def build_dense_daily_panel(
        self,
        session: Session,
    ) -> tuple[pd.DataFrame, dict[str, dict[str, Any]], list[date]]:
        """Construct a dense `(sku_id, order_date)` daily demand panel across all active SKUs."""
        skus_orm = session.scalars(
            select(SKU).where(SKU.status == SKUStatus.ACTIVE.value).order_by(SKU.sku_id)
        ).all()
        if not skus_orm:
            raise ValueError("Cannot build forecasting panel: 0 active SKUs in database.")

        suppliers_map = {
            s.supplier_id: float(s.average_lead_time_days) for s in session.scalars(select(Supplier)).all()
        }
        profiles_map = {
            p.sku_id: p for p in session.scalars(select(SKUProfile).where(SKUProfile.effective_to.is_(None))).all()
        }
        active_assns = session.scalars(
            select(SlotAssignment)
            .where(SlotAssignment.assignment_status == AssignmentStatus.ACTIVE.value)
            .order_by(SlotAssignment.slot_assignment_id)
        ).all()
        sku_wh_map: dict[str, str] = {}
        sku_loc_map: dict[str, str] = {}
        for a in active_assns:
            sku_wh_map.setdefault(a.sku_id, a.warehouse_id)
            sku_loc_map.setdefault(a.sku_id, a.location_id)

        default_lt = float(self.config.demand_analytics.default_lead_time_days)
        categories_sorted = sorted({s.category for s in skus_orm})
        cat_to_code = {c: idx for idx, c in enumerate(categories_sorted)}

        sku_meta: dict[str, dict[str, Any]] = {}
        for s in skus_orm:
            vol_m3 = (float(s.width_cm) * float(s.height_cm) * float(s.depth_cm)) / 1_000_000.0
            prof = profiles_map.get(s.sku_id)
            sku_meta[s.sku_id] = {
                "sku_id": s.sku_id,
                "sku_name": s.sku_name,
                "category": s.category,
                "category_code": float(cat_to_code[s.category]),
                "log_unit_price": float(math.log1p(max(0.0, float(s.unit_price)))),
                "unit_weight_kg": float(s.unit_weight_kg),
                "unit_volume_m3": float(vol_m3),
                "is_fragile": 1.0 if s.is_fragile else 0.0,
                "lead_time_days": float(suppliers_map.get(s.supplier_id, default_lt)),
                "historical_daily_demand": float(prof.average_daily_demand) if prof else 0.0,
                "historical_std_dev": float(prof.demand_std_dev) if prof else 0.0,
                "historical_velocity": str(prof.demand_velocity) if prof else DemandVelocityClass.SLOW.value,
                "historical_reorder_point": float(prof.reorder_point) if prof else 0.0,
                "target_days_of_supply": int(prof.target_days_of_supply) if prof else 7,
                "current_on_hand_qty": int(prof.current_on_hand_qty) if prof else 0,
                "active_warehouse_id": sku_wh_map.get(s.sku_id),
                "active_location_id": sku_loc_map.get(s.sku_id),
            }

        order_rows = session.execute(
            select(OrderLine.sku_id, Order.order_date, OrderLine.quantity).join(
                Order, OrderLine.order_id == Order.order_id
            )
        ).all()
        if not order_rows:
            raise ValueError("Cannot train SKU demand forecaster: 0 order lines found in database.")

        agg_daily: dict[tuple[str, date], float] = {}
        min_dt: date | None = None
        max_dt: date | None = None
        for sid, ord_dt, qty in order_rows:
            if sid not in sku_meta:
                continue
            d_val = ord_dt.date() if hasattr(ord_dt, "date") else date.fromisoformat(str(ord_dt)[:10])
            if min_dt is None or d_val < min_dt:
                min_dt = d_val
            if max_dt is None or d_val > max_dt:
                max_dt = d_val
            key = (str(sid), d_val)
            agg_daily[key] = agg_daily.get(key, 0.0) + float(qty)

        if min_dt is None or max_dt is None:
            raise ValueError("No valid order dates found for active SKUs.")

        # Ensure full contiguous calendar sequence from configured start date (if earlier) through max_dt
        cfg_start = date.fromisoformat(self.config.dataset.observation_start_date)
        start_dt = min(min_dt, cfg_start)
        total_days = (max_dt - start_dt).days + 1
        if total_days < 21:
            raise ValueError(f"Insufficient chronological history ({total_days} days); need >= 21 days.")

        all_dates = [start_dt + timedelta(days=i) for i in range(total_days)]
        sku_ids_sorted = sorted(sku_meta.keys())

        records: list[dict[str, Any]] = []
        for sid in sku_ids_sorted:
            meta = sku_meta[sid]
            for idx, d_val in enumerate(all_dates):
                dow = d_val.weekday()
                records.append(
                    {
                        "sku_id": sid,
                        "order_date": d_val,
                        "day_idx": idx,
                        "units_ordered": agg_daily.get((sid, d_val), 0.0),
                        "day_of_week": float(dow),
                        "is_weekend": 1.0 if dow >= 5 else 0.0,
                        "day_of_month": float(d_val.day),
                        "sin_dow": math.sin(2.0 * math.pi * dow / 7.0),
                        "cos_dow": math.cos(2.0 * math.pi * dow / 7.0),
                        # Constant scale (NOT total_days) so training, holdout evaluation and forward roll-out
                        # share an identical feature definition and no dataset-length information is encoded.
                        "day_index_norm": float(idx) / DAY_INDEX_SCALE,
                        "log_unit_price": meta["log_unit_price"],
                        "unit_weight_kg": meta["unit_weight_kg"],
                        "unit_volume_m3": meta["unit_volume_m3"],
                        "is_fragile": meta["is_fragile"],
                        "lead_time_days": meta["lead_time_days"],
                        "category_code": meta["category_code"],
                    }
                )

        panel_df = pd.DataFrame(records).sort_values(["sku_id", "day_idx"]).reset_index(drop=True)
        return panel_df, sku_meta, all_dates

    @staticmethod
    def engineer_causal_features(
        panel_df: pd.DataFrame,
        cutoff_date: date,
    ) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
        """Compute strictly causal lag/rolling features and train-only SKU priors (zero future leakage)."""
        df = panel_df.sort_values(["sku_id", "day_idx"]).copy()
        grp = df.groupby("sku_id", sort=False)["units_ordered"]

        df["lag_1"] = grp.shift(1).fillna(0.0)
        df["lag_2"] = grp.shift(2).fillna(0.0)
        df["lag_3"] = grp.shift(3).fillna(0.0)
        df["lag_7"] = grp.shift(7).fillna(0.0)
        df["lag_14"] = grp.shift(14).fillna(0.0)

        # Shift by 1 first so all rolling statistics use strictly t-1, t-2, ...
        df = df.reset_index(drop=True)
        shifted_grp = df.groupby("sku_id", sort=False)["lag_1"]
        df["rolling_mean_7"] = (
            shifted_grp.rolling(7, min_periods=1).mean().reset_index(level=0, drop=True).astype(float)
        )
        df["rolling_std_7"] = (
            shifted_grp.rolling(7, min_periods=2).std().reset_index(level=0, drop=True).fillna(0.0).astype(float)
        )
        df["rolling_mean_14"] = (
            shifted_grp.rolling(14, min_periods=1).mean().reset_index(level=0, drop=True).astype(float)
        )
        df["rolling_mean_28"] = (
            shifted_grp.rolling(28, min_periods=1).mean().reset_index(level=0, drop=True).astype(float)
        )
        df["rolling_max_14"] = (
            shifted_grp.rolling(14, min_periods=1).max().reset_index(level=0, drop=True).astype(float)
        )
        df["_lag_1_nz"] = (df["lag_1"] > 0).astype(float)
        df["nonzero_rate_14"] = (
            df.groupby("sku_id", sort=False)["_lag_1_nz"]
            .rolling(14, min_periods=1)
            .mean()
            .reset_index(level=0, drop=True)
            .astype(float)
        )
        df = df.drop(columns=["_lag_1_nz"])
        df["ewma_7"] = shifted_grp.ewm(span=7, adjust=False).mean().reset_index(level=0, drop=True).astype(float)
        df["momentum_7_28"] = df["rolling_mean_7"] / np.maximum(df["rolling_mean_28"], 0.1)

        # Compute per-SKU historical priors strictly within the training window (order_date <= cutoff_date)
        train_slice = df[df["order_date"] <= cutoff_date]
        sku_stats_df = (
            train_slice.groupby("sku_id")["units_ordered"]
            .agg(
                sku_train_mean="mean",
                sku_train_std=lambda s: float(s.std(ddof=0)),
                sku_train_nonzero_rate=lambda s: float((s > 0).mean()),
            )
            .reset_index()
        )
        dow_stats_df = (
            train_slice.groupby(["sku_id", "day_of_week"])["units_ordered"]
            .mean()
            .rename("sku_train_dow_mean")
            .reset_index()
        )

        df = df.merge(sku_stats_df, on="sku_id", how="left")
        df = df.merge(dow_stats_df, on=["sku_id", "day_of_week"], how="left")
        df["sku_train_mean"] = df["sku_train_mean"].fillna(0.0)
        df["sku_train_std"] = df["sku_train_std"].fillna(0.0)
        df["sku_train_nonzero_rate"] = df["sku_train_nonzero_rate"].fillna(0.0)
        df["sku_train_dow_mean"] = df["sku_train_dow_mean"].fillna(df["sku_train_mean"])

        # Export per-SKU priors lookup for multi-step forward inference
        sku_priors: dict[str, dict[str, Any]] = {}
        dow_lookup = {
            (str(r["sku_id"]), int(r["day_of_week"])): float(r["sku_train_dow_mean"])
            for _, r in dow_stats_df.iterrows()
        }
        for _, r in sku_stats_df.iterrows():
            sid = str(r["sku_id"])
            sku_priors[sid] = {
                "sku_train_mean": float(r["sku_train_mean"]),
                "sku_train_std": float(r["sku_train_std"]),
                "sku_train_nonzero_rate": float(r["sku_train_nonzero_rate"]),
                "dow_means": {d: dow_lookup.get((sid, d), float(r["sku_train_mean"])) for d in range(7)},
            }

        return df, sku_priors

    def train_and_evaluate(
        self,
        session: Session,
        profile: str | None = None,
        forecast_horizon_days: int | None = None,
        train_ratio: float | None = None,
        actor: str = "ML_FORECAST_PIPELINE",
    ) -> dict[str, Any]:
        """Train & compare Naive Baselines vs. ML models on chronological splits, log to MLflow, and save artifacts."""
        ml_cfg = self.config.ml_forecasting
        active_profile = (profile or self.config.dataset.profile).lower().strip()
        horizon = int(forecast_horizon_days or ml_cfg.forecast_horizon_days)
        ratio = float(train_ratio or ml_cfg.train_ratio)
        seed = int(self.config.project.seed)

        panel_df, sku_meta, all_dates = self.build_dense_daily_panel(session)
        total_days = len(all_dates)
        warmup_days = min(ml_cfg.warmup_lag_days, max(7, total_days // 5))
        cutoff_idx = max(warmup_days + 5, min(total_days - 3, int(math.floor(total_days * ratio)) - 1))

        warmup_end_dt = all_dates[warmup_days - 1]
        train_start_dt = all_dates[warmup_days]
        cutoff_dt = all_dates[cutoff_idx]
        test_start_dt = all_dates[cutoff_idx + 1]
        test_end_dt = all_dates[-1]

        feat_df, sku_priors = self.engineer_causal_features(panel_df, cutoff_date=cutoff_dt)

        train_df = feat_df[(feat_df["order_date"] >= train_start_dt) & (feat_df["order_date"] <= cutoff_dt)].copy()
        test_df = feat_df[feat_df["order_date"] >= test_start_dt].copy()

        # Hard chronological integrity verification
        max_tr = train_df["order_date"].max()
        min_te = test_df["order_date"].min()
        if not (max_tr < min_te):
            raise RuntimeError(f"Chronological split violation: max_train={max_tr} >= min_test={min_te}")

        split_info = {
            "observation_start_date": all_dates[0].isoformat(),
            "observation_end_date": all_dates[-1].isoformat(),
            "warmup_start_date": all_dates[0].isoformat(),
            "warmup_end_date": warmup_end_dt.isoformat(),
            "train_start_date": train_start_dt.isoformat(),
            "train_end_date": cutoff_dt.isoformat(),
            "test_start_date": test_start_dt.isoformat(),
            "test_end_date": test_end_dt.isoformat(),
            "total_calendar_days": total_days,
            "warmup_days": warmup_days,
            "train_days": (cutoff_dt - train_start_dt).days + 1,
            "test_days": (test_end_dt - test_start_dt).days + 1,
            "active_sku_count": len(sku_meta),
            "train_rows": int(len(train_df)),
            "test_rows": int(len(test_df)),
            "chronological_order_verified": True,
        }

        x_train = train_df[FEATURE_COLUMNS].to_numpy(dtype=np.float64)
        y_train = train_df["units_ordered"].to_numpy(dtype=np.float64)
        x_test = test_df[FEATURE_COLUMNS].to_numpy(dtype=np.float64)
        y_test = test_df["units_ordered"].to_numpy(dtype=np.float64)

        sku_ids_sorted = sorted(sku_meta.keys())
        test_days_count = int(split_info["test_days"])
        train_only_panel = panel_df[panel_df["order_date"] <= cutoff_dt].copy()
        train_only_dates = [d for d in all_dates if d <= cutoff_dt]
        actual_horizon_by_sku = test_df.groupby("sku_id")["units_ordered"].sum().to_dict()
        y_horizon_true = np.asarray(
            [float(actual_horizon_by_sku.get(sid, 0.0)) for sid in sku_ids_sorted],
            dtype=np.float64,
        )

        # Cutoff-origin multi-step Naive horizon projections (zero peeking beyond cutoff_dt)
        cutoff_last_by_sku: dict[str, float] = {}
        cutoff_ma7_by_sku: dict[str, float] = {}
        for sid, grp in train_only_panel.groupby("sku_id", sort=False):
            vals = grp["units_ordered"].to_numpy(dtype=np.float64)
            cutoff_last_by_sku[str(sid)] = float(vals[-1]) * test_days_count if vals.size else 0.0
            cutoff_ma7_by_sku[str(sid)] = float(np.mean(vals[-7:])) * test_days_count if vals.size else 0.0

        naive_last_horizon_preds = np.asarray(
            [cutoff_last_by_sku.get(sid, 0.0) for sid in sku_ids_sorted], dtype=np.float64
        )
        naive_ma7_horizon_preds = np.asarray(
            [cutoff_ma7_by_sku.get(sid, 0.0) for sid in sku_ids_sorted], dtype=np.float64
        )

        # 1. Naive Baselines (1-step daily)
        naive_last_preds = test_df["lag_1"].to_numpy(dtype=np.float64)
        naive_ma7_preds = test_df["rolling_mean_7"].to_numpy(dtype=np.float64)
        # Strong reference baselines for intermittent demand: all-zero forecast and the SKU's TRAIN-window mean.
        # (Daily WAPE > 100% means a model is worse than simply predicting zero, so zero MUST be on the leaderboard.)
        naive_zero_preds = np.zeros(len(test_df), dtype=np.float64)
        naive_zero_horizon_preds = np.zeros(len(sku_ids_sorted), dtype=np.float64)
        train_mean_preds = test_df["sku_train_mean"].to_numpy(dtype=np.float64)
        train_mean_by_sku = {
            sid: float(sku_priors.get(sid, {}).get("sku_train_mean", 0.0)) for sid in sku_ids_sorted
        }
        train_mean_horizon_preds = np.asarray(
            [train_mean_by_sku[sid] * test_days_count for sid in sku_ids_sorted], dtype=np.float64
        )

        # 2. ML Candidate 1: Ridge Regression
        ridge_model = Pipeline(
            [
                ("scaler", StandardScaler()),
                ("ridge", Ridge(alpha=25.0, random_state=seed)),
            ]
        )
        ridge_model.fit(x_train, y_train)
        ridge_preds = np.maximum(0.0, ridge_model.predict(x_test))
        ridge_roll = self._roll_forward_forecasts(
            panel_df=train_only_panel,
            sku_meta=sku_meta,
            sku_priors=sku_priors,
            all_dates=train_only_dates,
            estimator=ridge_model,
            horizon_days=test_days_count,
        )
        ridge_roll_map = {r["sku_id"]: float(r["forecast_total_units"]) for r in ridge_roll}
        ridge_horizon_preds = np.asarray(
            [ridge_roll_map.get(sid, 0.0) for sid in sku_ids_sorted], dtype=np.float64
        )

        # 3. ML Candidate 2: Poisson HistGradientBoostingRegressor
        hgb_params = {
            "loss": "poisson",
            "max_iter": 180,
            "learning_rate": 0.05,
            "max_leaf_nodes": 31,
            "min_samples_leaf": 25,
            "l2_regularization": 2.0,
            "random_state": seed,
        }
        hgb_model = HistGradientBoostingRegressor(**hgb_params)
        hgb_model.fit(x_train, y_train)
        hgb_preds = np.maximum(0.0, hgb_model.predict(x_test))
        hgb_roll = self._roll_forward_forecasts(
            panel_df=train_only_panel,
            sku_meta=sku_meta,
            sku_priors=sku_priors,
            all_dates=train_only_dates,
            estimator=hgb_model,
            horizon_days=test_days_count,
        )
        hgb_roll_map = {r["sku_id"]: float(r["forecast_total_units"]) for r in hgb_roll}
        hgb_horizon_preds = np.asarray([hgb_roll_map.get(sid, 0.0) for sid in sku_ids_sorted], dtype=np.float64)

        def _evaluate_predictions(daily_preds: np.ndarray, horizon_preds: np.ndarray) -> dict[str, float]:
            daily_m = compute_regression_metrics(y_test, np.maximum(0.0, daily_preds))
            horizon_m = compute_regression_metrics(y_horizon_true, np.maximum(0.0, horizon_preds))
            return {
                "daily_mae": daily_m["mae"],
                "daily_rmse": daily_m["rmse"],
                "daily_wape_pct": daily_m["wape_pct"],
                "horizon_mae": horizon_m["mae"],
                "horizon_rmse": horizon_m["rmse"],
                "horizon_wape_pct": horizon_m["wape_pct"],
            }

        candidate_specs: list[tuple[str, str, dict[str, Any], np.ndarray, np.ndarray, Any]] = [
            (
                "NAIVE_ZERO_FORECAST",
                "NAIVE_BASELINE",
                {"rule": "y_hat(t) = 0"},
                naive_zero_preds,
                naive_zero_horizon_preds,
                None,
            ),
            (
                "NAIVE_SKU_TRAIN_MEAN",
                "NAIVE_BASELINE",
                {"rule": "y_hat(t) = mean of SKU demand over TRAIN window only; horizon = H * that mean"},
                train_mean_preds,
                train_mean_horizon_preds,
                None,
            ),
            (
                "NAIVE_LAST_OBSERVATION",
                "NAIVE_BASELINE",
                {"rule": "y_hat(t) = y(t-1); horizon = H * y(T)"},
                naive_last_preds,
                naive_last_horizon_preds,
                None,
            ),
            (
                "NAIVE_MOVING_AVG_7D",
                "NAIVE_BASELINE",
                {"rule": "y_hat(t) = mean(y(t-7..t-1)); horizon = H * mean(y(T-6..T))"},
                naive_ma7_preds,
                naive_ma7_horizon_preds,
                None,
            ),
            (
                "RIDGE_REGRESSION",
                "MACHINE_LEARNING",
                {"alpha": 25.0, "features": len(FEATURE_COLUMNS)},
                ridge_preds,
                ridge_horizon_preds,
                ridge_model,
            ),
            (
                "HIST_GRADIENT_BOOSTING_POISSON",
                "MACHINE_LEARNING",
                {**hgb_params, "features": len(FEATURE_COLUMNS)},
                hgb_preds,
                hgb_horizon_preds,
                hgb_model,
            ),
        ]

        # Configure local zero-dependency MLflow SQLite tracking store
        os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
        os.environ.setdefault("MLFLOW_DISABLE_TELEMETRY", "true")
        os.environ.setdefault("GIT_PYTHON_REFRESH", "quiet")
        import mlflow

        self.mlruns_dir.mkdir(parents=True, exist_ok=True)
        mlflow_artifacts_dir = self.mlruns_dir / "artifacts"
        mlflow_artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)

        tracking_uri = f"sqlite:///{(self.mlruns_dir / 'mlflow.db').resolve().as_posix()}"
        artifact_root_uri = mlflow_artifacts_dir.resolve().as_uri()
        mlflow.set_tracking_uri(tracking_uri)
        exp = mlflow.get_experiment_by_name(ml_cfg.mlflow_experiment_name)
        if exp is None:
            exp_id = mlflow.create_experiment(
                ml_cfg.mlflow_experiment_name,
                artifact_location=artifact_root_uri,
            )
        else:
            exp_id = exp.experiment_id

        leaderboard: list[dict[str, Any]] = []
        trained_ml_estimators: dict[str, Any] = {}

        for model_name, family, params, preds, h_preds, estimator in candidate_specs:
            metrics = _evaluate_predictions(preds, h_preds)
            run_id: str | None = None
            with mlflow.start_run(experiment_id=exp_id, run_name=f"{active_profile}_{model_name}") as active_run:
                run_id = active_run.info.run_id
                mlflow.set_tags(
                    {
                        "profile": active_profile,
                        "model_family": family,
                        "chronological_split": "true",
                    }
                )
                mlflow.log_params(
                    {
                        "profile": active_profile,
                        "seed": seed,
                        "model_name": model_name,
                        "train_start": split_info["train_start_date"],
                        "train_end": split_info["train_end_date"],
                        "test_start": split_info["test_start_date"],
                        "test_end": split_info["test_end_date"],
                        "train_rows": split_info["train_rows"],
                        "test_rows": split_info["test_rows"],
                        "forecast_horizon_days": horizon,
                        **{k: str(v) for k, v in params.items()},
                    }
                )
                mlflow.log_metrics(metrics)

            if estimator is not None:
                trained_ml_estimators[model_name] = estimator

            leaderboard.append(
                {
                    "model_name": model_name,
                    "model_family": family,
                    "mlflow_run_id": run_id,
                    **metrics,
                    "is_champion": False,
                }
            )

        # Select champion ML model by cumulative-horizon WAPE (the quantity actually consumed by slotting and
        # replenishment: average daily demand / reorder point over the horizon); tie-breaker: daily MAE.
        ml_entries = [m for m in leaderboard if m["model_family"] == "MACHINE_LEARNING"]
        champion_entry = min(ml_entries, key=lambda m: (m["horizon_wape_pct"], m["daily_mae"]))
        champion_entry["is_champion"] = True
        champion_name = str(champion_entry["model_name"])
        champion_estimator = trained_ml_estimators[champion_name]

        # Compare the champion against the STRONGEST naive baseline on the same horizon metric (honest reference)
        naive_entries = [m for m in leaderboard if m["model_family"] == "NAIVE_BASELINE"]
        naive_ref = min(naive_entries, key=lambda m: (m["horizon_wape_pct"], m["daily_mae"]))

        def _rel_improvement(base: float, new: float) -> float:
            return round(((base - new) / base) * 100.0, 2) if base > 1e-9 else 0.0

        wape_imp_pct = _rel_improvement(naive_ref["horizon_wape_pct"], champion_entry["horizon_wape_pct"])
        mae_imp_pct = _rel_improvement(naive_ref["horizon_mae"], champion_entry["horizon_mae"])
        best_daily_naive = min(naive_entries, key=lambda m: (m["daily_wape_pct"], m["daily_mae"]))
        daily_wape_imp_pct = _rel_improvement(best_daily_naive["daily_wape_pct"], champion_entry["daily_wape_pct"])
        ml_beats_best_baseline = bool(
            champion_entry["horizon_wape_pct"] < naive_ref["horizon_wape_pct"]
            and champion_entry["daily_wape_pct"] <= best_daily_naive["daily_wape_pct"]
        )

        # Refit champion architecture on full observed post-warmup window so forward forecasts use latest data
        full_feat_df, full_priors = self.engineer_causal_features(panel_df, cutoff_date=all_dates[-1])
        full_sup_df = full_feat_df[full_feat_df["order_date"] >= train_start_dt].copy()

        if champion_name == "HIST_GRADIENT_BOOSTING_POISSON":
            production_estimator: Any = HistGradientBoostingRegressor(**hgb_params)
        else:
            production_estimator = Pipeline(
                [
                    ("scaler", StandardScaler()),
                    ("ridge", Ridge(alpha=25.0, random_state=seed)),
                ]
            )
        production_estimator.fit(
            full_sup_df[FEATURE_COLUMNS].to_numpy(dtype=np.float64),
            full_sup_df["units_ordered"].to_numpy(dtype=np.float64),
        )

        # Generate multi-step forward SKU forecasts for H days beyond all_dates[-1]
        sku_forecasts = self._roll_forward_forecasts(
            panel_df=panel_df,
            sku_meta=sku_meta,
            sku_priors=full_priors,
            all_dates=all_dates,
            estimator=production_estimator,
            horizon_days=horizon,
        )

        trained_ts = utc_now_naive().isoformat()
        joblib_path = self._model_path(active_profile)
        latest_joblib_path = self.models_dir / "demand_forecaster_latest.joblib"
        eval_path = self._eval_path(active_profile)
        latest_eval_path = self.models_dir / "forecast_evaluation_latest.json"
        forecasts_path = self._forecasts_path(active_profile)
        latest_forecasts_path = self.models_dir / "sku_forecasts_latest.json"

        bundle = {
            "profile": active_profile,
            "seed": seed,
            "trained_at": trained_ts,
            "champion_model_name": champion_name,
            "champion_mlflow_run_id": champion_entry["mlflow_run_id"],
            "feature_columns": FEATURE_COLUMNS,
            "holdout_estimator": champion_estimator,
            "production_estimator": production_estimator,
            "sku_priors": full_priors,
            "split_info": split_info,
            "leaderboard": leaderboard,
            "forecast_horizon_days": horizon,
        }
        joblib.dump(bundle, joblib_path)
        joblib.dump(bundle, latest_joblib_path)

        surging_skus = [
            f
            for f in sku_forecasts
            if f["surge_ratio"] >= ml_cfg.surge_threshold_ratio and f["forecast_daily_demand"] >= 0.5
        ]
        surging_skus_sorted = sorted(
            surging_skus,
            key=lambda f: (-f["surge_ratio"], -f["forecast_daily_demand"], f["sku_id"]),
        )
        vel_shift_count = sum(1 for f in sku_forecasts if f["velocity_tier_changed"])

        eval_summary = {
            "status": "COMPLETED",
            "profile": active_profile,
            "seed": seed,
            "champion_model_name": champion_name,
            "baseline_reference_model": str(naive_ref["model_name"]),
            "wape_improvement_vs_naive_pct": wape_imp_pct,
            "daily_wape_improvement_vs_naive_pct": daily_wape_imp_pct,
            "mae_improvement_vs_naive_pct": mae_imp_pct,
            "ml_beats_best_naive_baseline": ml_beats_best_baseline,
            "mlflow_experiment_name": ml_cfg.mlflow_experiment_name,
            "mlflow_tracking_uri": tracking_uri,
            "champion_mlflow_run_id": champion_entry["mlflow_run_id"],
            "artifact_joblib_path": joblib_path.as_posix(),
            "evaluation_report_path": eval_path.as_posix(),
            "forecasts_path": forecasts_path.as_posix(),
            "split_info": split_info,
            "leaderboard": leaderboard,
            "surging_sku_count": len(surging_skus_sorted),
            "velocity_shift_sku_count": vel_shift_count,
            "top_surging_skus": surging_skus_sorted[:10],
            "trained_at": trained_ts,
        }

        eval_json = json.dumps(eval_summary, indent=2)
        eval_path.write_text(eval_json, encoding="utf-8")
        latest_eval_path.write_text(eval_json, encoding="utf-8")

        forecasts_payload = {
            "profile": active_profile,
            "trained_at": trained_ts,
            "champion_model_name": champion_name,
            "forecast_horizon_days": horizon,
            "forecasts": sku_forecasts,
        }
        f_json = json.dumps(forecasts_payload, indent=2)
        forecasts_path.write_text(f_json, encoding="utf-8")
        latest_forecasts_path.write_text(f_json, encoding="utf-8")

        # Log evaluation report artifact onto the champion MLflow run
        if champion_entry["mlflow_run_id"]:
            with mlflow.start_run(run_id=champion_entry["mlflow_run_id"]):
                mlflow.log_metric("wape_improvement_vs_naive_pct", wape_imp_pct)
                mlflow.log_metric("mae_improvement_vs_naive_pct", mae_imp_pct)
                mlflow.log_artifact(str(eval_path))

        self.audit_service.log_event(
            session=session,
            event_type="ML_FORECAST_MODEL_TRAINED",
            entity_type="ML_MODEL",
            entity_id=f"{champion_name}:{active_profile}",
            actor=actor,
            previous_state=None,
            new_state={
                "champion_model": champion_name,
                "mlflow_run_id": champion_entry["mlflow_run_id"],
                "daily_mae": champion_entry["daily_mae"],
                "daily_rmse": champion_entry["daily_rmse"],
                "daily_wape_pct": champion_entry["daily_wape_pct"],
                "naive_ma7_wape_pct": naive_ref["daily_wape_pct"],
                "wape_improvement_vs_naive_pct": wape_imp_pct,
                "surging_sku_count": len(surging_skus_sorted),
            },
        )
        session.flush()
        return eval_summary

    def _roll_forward_forecasts(
        self,
        panel_df: pd.DataFrame,
        sku_meta: dict[str, dict[str, Any]],
        sku_priors: dict[str, dict[str, Any]],
        all_dates: list[date],
        estimator: Any,
        horizon_days: int,
    ) -> list[dict[str, Any]]:
        """Autoregressively forecast next `horizon_days` for every active SKU and derive slotting/ROP guidance."""
        sku_ids = sorted(sku_meta.keys())
        total_hist_days = len(all_dates)
        last_date = all_dates[-1]

        # Build recent 28-day history buffer per SKU
        history_by_sku: dict[str, list[float]] = {}
        ewma_by_sku: dict[str, float] = {}
        for sid, grp in panel_df.groupby("sku_id", sort=True):
            vals = grp["units_ordered"].astype(float).tolist()
            history_by_sku[str(sid)] = vals[-28:] if len(vals) >= 28 else ([0.0] * (28 - len(vals)) + vals)
            ewm_val = float(pd.Series(vals).ewm(span=7, adjust=False).mean().iloc[-1])
            ewma_by_sku[str(sid)] = ewm_val

        alpha_ewm = 2.0 / (7.0 + 1.0)
        preds_by_sku: dict[str, list[tuple[str, float]]] = {sid: [] for sid in sku_ids}

        for step in range(1, horizon_days + 1):
            f_date = last_date + timedelta(days=step)
            dow = f_date.weekday()
            day_idx_norm = float(total_hist_days + step - 1) / DAY_INDEX_SCALE
            sin_dow = math.sin(2.0 * math.pi * dow / 7.0)
            cos_dow = math.cos(2.0 * math.pi * dow / 7.0)

            step_rows: list[list[float]] = []
            for sid in sku_ids:
                buf = history_by_sku[sid]
                meta = sku_meta[sid]
                pri = sku_priors.get(
                    sid,
                    {
                        "sku_train_mean": 0.0,
                        "sku_train_std": 0.0,
                        "sku_train_nonzero_rate": 0.0,
                        "dow_means": dict.fromkeys(range(7), 0.0),
                    },
                )
                w7 = buf[-7:]
                w14 = buf[-14:]
                w28 = buf[-28:]

                lag_1 = buf[-1]
                lag_2 = buf[-2]
                lag_3 = buf[-3]
                lag_7 = buf[-7]
                lag_14 = buf[-14]
                rm7 = float(np.mean(w7))
                rs7 = float(np.std(w7, ddof=1)) if len(w7) >= 2 else 0.0
                rm14 = float(np.mean(w14))
                rm28 = float(np.mean(w28))
                rmax14 = float(np.max(w14))
                nz14 = float(np.mean([1.0 if v > 0 else 0.0 for v in w14]))
                ewm7 = ewma_by_sku[sid]
                mom = rm7 / max(rm28, 0.1)

                step_rows.append(
                    [
                        lag_1,
                        lag_2,
                        lag_3,
                        lag_7,
                        lag_14,
                        rm7,
                        rs7,
                        rm14,
                        rm28,
                        rmax14,
                        nz14,
                        ewm7,
                        mom,
                        pri["sku_train_mean"],
                        pri["sku_train_std"],
                        pri["sku_train_nonzero_rate"],
                        pri["dow_means"].get(dow, pri["sku_train_mean"]),
                        float(dow),
                        1.0 if dow >= 5 else 0.0,
                        float(f_date.day),
                        sin_dow,
                        cos_dow,
                        day_idx_norm,
                        meta["log_unit_price"],
                        meta["unit_weight_kg"],
                        meta["unit_volume_m3"],
                        meta["is_fragile"],
                        meta["lead_time_days"],
                        meta["category_code"],
                    ]
                )

            x_step = np.asarray(step_rows, dtype=np.float64)
            y_hat_step = np.maximum(0.0, estimator.predict(x_step))

            for sid, pred_val in zip(sku_ids, y_hat_step, strict=True):
                clean_p = round(float(pred_val), 4)
                preds_by_sku[sid].append((f_date.isoformat(), clean_p))
                history_by_sku[sid].append(clean_p)
                if len(history_by_sku[sid]) > 28:
                    history_by_sku[sid].pop(0)
                ewma_by_sku[sid] = alpha_ewm * clean_p + (1.0 - alpha_ewm) * ewma_by_sku[sid]

        # Compute forecast percentiles across all SKUs to assign forecast velocity tiers
        f_adv_map = {
            sid: float(np.mean([v for _, v in preds_by_sku[sid]])) if preds_by_sku[sid] else 0.0 for sid in sku_ids
        }
        sorted_by_f_adv = sorted(sku_ids, key=lambda s: (f_adv_map[s], s))
        n_skus = max(1, len(sorted_by_f_adv))
        f_pct_map: dict[str, float] = {}
        for rank_idx, sid in enumerate(sorted_by_f_adv):
            f_pct_map[sid] = round((rank_idx + 1) / n_skus, 4)

        vel_cfg = self.config.demand_analytics.velocity_percentiles
        z_score = float(self.config.demand_analytics.safety_stock_z_score)
        ml_cfg = self.config.ml_forecasting

        vel_order = {
            DemandVelocityClass.SLOW.value: 0,
            DemandVelocityClass.MEDIUM.value: 1,
            DemandVelocityClass.FAST.value: 2,
        }

        results: list[dict[str, Any]] = []
        for sid in sku_ids:
            meta = sku_meta[sid]
            traj = preds_by_sku[sid]
            vals = [v for _, v in traj]
            f_adv = round(float(np.mean(vals)) if vals else 0.0, 4)
            f_tot = round(float(np.sum(vals)) if vals else 0.0, 2)
            # Combine forecast trajectory variation with historical residual uncertainty for safety stock
            traj_std = float(np.std(vals, ddof=0)) if vals else 0.0
            eff_std = round(max(traj_std, float(meta["historical_std_dev"]) * 0.85), 4)

            h_adv = round(float(meta["historical_daily_demand"]), 4)
            surge_ratio = (
                round(f_adv / h_adv, 3) if h_adv > 0.05 else (round(1.0 + f_adv, 3) if f_adv > 0.05 else 1.0)
            )

            pct = f_pct_map[sid]
            if pct >= vel_cfg.fast_min_percentile and f_adv > 0.0:
                f_vel = DemandVelocityClass.FAST.value
            elif pct >= vel_cfg.medium_min_percentile and f_adv > 0.0:
                f_vel = DemandVelocityClass.MEDIUM.value
            else:
                f_vel = DemandVelocityClass.SLOW.value

            h_vel = meta["historical_velocity"]
            tier_changed = f_vel != h_vel

            lt = float(meta["lead_time_days"])
            f_safety = z_score * eff_std * math.sqrt(max(lt, 1.0))
            f_rop = round(f_adv * lt + f_safety, 2) if f_adv > 0.0 else 0.0
            h_rop = round(float(meta["historical_reorder_point"]), 2)

            if vel_order.get(f_vel, 0) > vel_order.get(h_vel, 0) or (
                surge_ratio >= ml_cfg.surge_threshold_ratio and f_adv >= 0.5
            ):
                slot_rec = "PROMOTE_TO_DOCK_PROXIMATE_FAST_PICK"
            elif vel_order.get(f_vel, 0) < vel_order.get(h_vel, 0) or (
                surge_ratio <= ml_cfg.decline_threshold_ratio and h_adv >= 0.5
            ):
                slot_rec = "REALLOCATE_TO_GENERAL_OR_RESERVE"
            else:
                slot_rec = "MAINTAIN_CURRENT_VELOCITY_ZONE"

            if f_rop > h_rop * 1.15 and f_rop - h_rop >= 1.0:
                rep_rec = "INCREASE_ROP_PROACTIVE_REPLENISHMENT"
            elif h_rop > 0 and f_rop < h_rop * 0.85:
                rep_rec = "TRIM_SAFETY_BUFFER_REDUCE_CYCLE_STOCK"
            else:
                rep_rec = "MAINTAIN_STANDARD_REORDER_POLICY"

            results.append(
                {
                    "sku_id": sid,
                    "sku_name": meta["sku_name"],
                    "category": meta["category"],
                    "active_warehouse_id": meta["active_warehouse_id"],
                    "active_location_id": meta["active_location_id"],
                    "forecast_horizon_days": horizon_days,
                    "historical_daily_demand": h_adv,
                    "forecast_daily_demand": f_adv,
                    "forecast_total_units": f_tot,
                    "forecast_std_dev": eff_std,
                    "surge_ratio": surge_ratio,
                    "historical_velocity": h_vel,
                    "forecast_velocity": f_vel,
                    "forecast_velocity_percentile": pct,
                    "velocity_tier_changed": tier_changed,
                    "historical_reorder_point": h_rop,
                    "forecast_reorder_point": f_rop,
                    "slotting_recommendation": slot_rec,
                    "replenishment_recommendation": rep_rec,
                    "daily_forecast_trajectory": [
                        {"forecast_date": d_str, "predicted_units": u_val} for d_str, u_val in traj
                    ],
                }
            )

        return results

    def get_status(self, profile: str | None = None) -> dict[str, Any]:
        """Return ML forecaster readiness, artifact path, and latest evaluation leaderboard if trained."""
        active_profile = (profile or self.config.dataset.profile).lower().strip()
        j_path = self._model_path(active_profile)
        e_path = self._eval_path(active_profile)
        if not j_path.exists():
            j_path = self.models_dir / "demand_forecaster_latest.joblib"
            e_path = self.models_dir / "forecast_evaluation_latest.json"

        has_artifacts = j_path.exists() and e_path.exists()
        eval_summary: dict[str, Any] | None = None
        if e_path.exists():
            try:
                eval_summary = json.loads(e_path.read_text(encoding="utf-8"))
            except ValueError:
                eval_summary = None

        return {
            "enabled": True,
            "trained_artifacts_present": has_artifacts,
            "profile": active_profile,
            "artifact_joblib_path": self._model_path(active_profile).as_posix(),
            "mlflow_tracking_uri": f"sqlite:///{(self.mlruns_dir / 'mlflow.db').resolve().as_posix()}",
            "evaluation_summary": eval_summary,
        }

    def list_forecasts(
        self,
        profile: str | None = None,
        sku_id: str | None = None,
        warehouse_id: str | None = None,
        velocity_shift_only: bool = False,
        surging_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Load persisted SKU-level ML demand forecasts with optional filtering."""
        active_profile = (profile or self.config.dataset.profile).lower().strip()
        f_path = self._forecasts_path(active_profile)
        if not f_path.exists():
            f_path = self.models_dir / "sku_forecasts_latest.json"
        if not f_path.exists():
            return []

        payload = json.loads(f_path.read_text(encoding="utf-8"))
        items: list[dict[str, Any]] = list(payload.get("forecasts") or [])

        if sku_id:
            target = sku_id.strip().upper()
            items = [f for f in items if f["sku_id"].upper() == target]
        if warehouse_id:
            w_target = warehouse_id.strip().upper()
            items = [f for f in items if (f.get("active_warehouse_id") or "").upper() == w_target]
        if velocity_shift_only:
            items = [f for f in items if f.get("velocity_tier_changed")]
        if surging_only:
            thr = self.config.ml_forecasting.surge_threshold_ratio
            items = [
                f
                for f in items
                if float(f.get("surge_ratio", 1.0)) >= thr and float(f.get("forecast_daily_demand", 0.0)) >= 0.5
            ]

        return items

    def get_forecast_lookup(
        self,
        session: Session,
        profile: str | None = None,
        auto_train_if_missing: bool = True,
    ) -> dict[str, dict[str, Any]]:
        """Return `{sku_id: forecast_dict}` for integration into Replenishment and Slotting Optimization."""
        items = self.list_forecasts(profile=profile)
        if not items and auto_train_if_missing:
            self.train_and_evaluate(session=session, profile=profile, actor="AUTO_FORECAST_HOOK")
            items = self.list_forecasts(profile=profile)
        return {f["sku_id"]: f for f in items}
