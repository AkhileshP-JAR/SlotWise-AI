"""Deterministic SKU demand velocity, ABC/XYZ classification, and reorder parameter service.

Implements Section 8.1 and Section 8.8 business formulas as a pure, reusable service
shared identically across synthetic data derivation, data validation, and operational analytics.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from backend.app.config import DemandAnalyticsConfig
from backend.app.models.enums import ABCClass, DemandVelocityClass, XYZClass
from backend.app.utils.ids import format_profile_id


class DemandAnalyticsService:
    """Computes SKU analytical profiles strictly from transactional order-line history."""

    def __init__(self, config: DemandAnalyticsConfig | None = None) -> None:
        self.config = config or DemandAnalyticsConfig()

    def compute_profiles_dataframe(
        self,
        skus_df: pd.DataFrame,
        orders_df: pd.DataFrame,
        order_lines_df: pd.DataFrame,
        suppliers_df: pd.DataFrame | None,
        observation_start_date: str,
        observation_days: int,
        effective_from: datetime,
        profile_version: int = 1,
    ) -> pd.DataFrame:
        """Compute deterministic SKUProfile records from master SKUs and historical order lines."""
        if observation_days <= 0:
            raise ValueError("observation_days must be strictly positive")

        base_skus = (
            skus_df[["sku_id", "unit_price", "supplier_id"]]
            .copy()
            .sort_values("sku_id", kind="mergesort")
            .reset_index(drop=True)
        )

        # Resolve supplier lead time
        if suppliers_df is not None and not suppliers_df.empty:
            sup_map = suppliers_df.set_index("supplier_id")["average_lead_time_days"].to_dict()
            base_skus["lead_time_days"] = (
                base_skus["supplier_id"].map(sup_map).fillna(self.config.default_lead_time_days).astype(float)
            )
        else:
            base_skus["lead_time_days"] = float(self.config.default_lead_time_days)

        start_dt = pd.Timestamp(observation_start_date).normalize()
        end_dt = start_dt + pd.Timedelta(days=observation_days - 1)

        if order_lines_df.empty or orders_df.empty:
            agg_df = pd.DataFrame(
                {
                    "sku_id": base_skus["sku_id"],
                    "total_units_sold": 0,
                    "total_order_lines": 0,
                    "total_revenue": 0.0,
                    "demand_std_dev": 0.0,
                    "peak_7d_adv": 0.0,
                }
            )
        else:
            order_dates = orders_df[["order_id", "order_date"]].copy()
            order_dates["day_index"] = (
                pd.to_datetime(order_dates["order_date"]).dt.normalize() - start_dt
            ).dt.days
            merged = order_lines_df[["order_id", "sku_id", "quantity", "line_value"]].merge(
                order_dates[["order_id", "day_index"]], on="order_id", how="inner"
            )
            merged = merged[(merged["day_index"] >= 0) & (merged["day_index"] < observation_days)]

            totals = merged.groupby("sku_id", as_index=False).agg(
                total_units_sold=("quantity", "sum"),
                total_order_lines=("quantity", "count"),
                total_revenue=("line_value", "sum"),
            )

            # Build dense (num_skus x observation_days) daily demand matrix so zero-demand days are included
            sku_to_idx = {sid: idx for idx, sid in enumerate(base_skus["sku_id"].tolist())}
            daily_grouped = merged.groupby(["sku_id", "day_index"], as_index=False)["quantity"].sum()
            dense_daily = np.zeros((len(base_skus), observation_days), dtype=np.float64)
            valid_rows = daily_grouped["sku_id"].isin(sku_to_idx)
            daily_valid = daily_grouped[valid_rows]
            if not daily_valid.empty:
                r_idx = daily_valid["sku_id"].map(sku_to_idx).to_numpy(dtype=np.int64)
                c_idx = daily_valid["day_index"].to_numpy(dtype=np.int64)
                vals = daily_valid["quantity"].to_numpy(dtype=np.float64)
                dense_daily[r_idx, c_idx] = vals

            ddof = 1 if observation_days > 1 else 0
            std_devs = np.std(dense_daily, axis=1, ddof=ddof)

            # Rolling 7-day peak demand average
            window = min(7, observation_days)
            if window > 1:
                csum = np.cumsum(dense_daily, axis=1)
                rolling_sums = np.empty((len(base_skus), observation_days - window + 1), dtype=np.float64)
                rolling_sums[:, 0] = csum[:, window - 1]
                rolling_sums[:, 1:] = csum[:, window:] - csum[:, :-window]
                peak_7d_adv = np.max(rolling_sums, axis=1) / float(window)
            else:
                peak_7d_adv = np.max(dense_daily, axis=1)

            stats_df = pd.DataFrame(
                {
                    "sku_id": base_skus["sku_id"],
                    "demand_std_dev": std_devs,
                    "peak_7d_adv": peak_7d_adv,
                }
            )
            agg_df = stats_df.merge(totals, on="sku_id", how="left").fillna(
                {"total_units_sold": 0, "total_order_lines": 0, "total_revenue": 0.0}
            )
            agg_df["total_units_sold"] = agg_df["total_units_sold"].astype(int)
            agg_df["total_order_lines"] = agg_df["total_order_lines"].astype(int)

        df = base_skus.merge(agg_df, on="sku_id", how="left")
        df["average_daily_demand"] = df["total_units_sold"] / float(observation_days)
        df["pick_frequency"] = df["total_order_lines"] / float(observation_days)

        # Deterministic velocity percentile (composite of ADV rank and pick frequency rank)
        n = len(df)
        if n > 1:
            adv_order = df.sort_values(["average_daily_demand", "sku_id"], kind="mergesort").index
            freq_order = df.sort_values(["pick_frequency", "sku_id"], kind="mergesort").index
            adv_pct = np.zeros(n, dtype=np.float64)
            freq_pct = np.zeros(n, dtype=np.float64)
            adv_pct[adv_order] = np.linspace(0.0, 1.0, n)
            freq_pct[freq_order] = np.linspace(0.0, 1.0, n)
            raw_comp = 0.5 * adv_pct + 0.5 * freq_pct
            df["_raw_comp"] = raw_comp
            comp_order = df.sort_values(["_raw_comp", "sku_id"], kind="mergesort").index
            vel_pct = np.zeros(n, dtype=np.float64)
            vel_pct[comp_order] = np.linspace(0.0, 1.0, n)
            df.drop(columns=["_raw_comp"], inplace=True)
        else:
            vel_pct = np.array([1.0 if df["total_units_sold"].iloc[0] > 0 else 0.0])

        # Zero-demand SKUs always receive 0.0 velocity percentile and SLOW class
        zero_mask = df["total_units_sold"] == 0
        vel_pct = np.where(zero_mask, 0.0, vel_pct)
        df["velocity_percentile"] = np.round(vel_pct, 6)

        fast_thr = self.config.velocity_percentiles.fast_min_percentile
        med_thr = self.config.velocity_percentiles.medium_min_percentile
        df["demand_velocity"] = np.where(
            zero_mask,
            DemandVelocityClass.SLOW.value,
            np.where(
                df["velocity_percentile"] >= fast_thr,
                DemandVelocityClass.FAST.value,
                np.where(
                    df["velocity_percentile"] >= med_thr,
                    DemandVelocityClass.MEDIUM.value,
                    DemandVelocityClass.SLOW.value,
                ),
            ),
        )

        # ABC classification based strictly on cumulative revenue value
        abc_sorted = df.sort_values(["total_revenue", "sku_id"], ascending=[False, True], kind="mergesort").copy()
        grand_revenue = float(abc_sorted["total_revenue"].sum())
        if grand_revenue > 0.0:
            cum_rev = abc_sorted["total_revenue"].cumsum()
            prev_share = (cum_rev - abc_sorted["total_revenue"]) / grand_revenue
            a_thr = self.config.abc_thresholds.a_cumulative_value_share
            b_thr = self.config.abc_thresholds.b_cumulative_value_share
            abc_labels = np.where(
                abc_sorted["total_revenue"] <= 0.0,
                ABCClass.C.value,
                np.where(
                    prev_share < a_thr,
                    ABCClass.A.value,
                    np.where(prev_share < b_thr, ABCClass.B.value, ABCClass.C.value),
                ),
            )
            abc_sorted["abc_class"] = abc_labels
        else:
            abc_sorted["abc_class"] = ABCClass.C.value
        df["abc_class"] = abc_sorted.sort_index()["abc_class"]

        # XYZ classification based on Coefficient of Variation (CV = std_dev / ADV)
        adv_arr = df["average_daily_demand"].to_numpy(dtype=np.float64)
        std_arr = df["demand_std_dev"].to_numpy(dtype=np.float64)
        with np.errstate(divide="ignore", invalid="ignore"):
            cv_arr = np.where(adv_arr > 0.0, std_arr / adv_arr, np.inf)

        x_max = self.config.xyz_cv_thresholds.x_max_cv
        y_max = self.config.xyz_cv_thresholds.y_max_cv
        df["xyz_class"] = np.where(
            adv_arr <= 0.0,
            XYZClass.Z.value,
            np.where(
                cv_arr <= x_max,
                XYZClass.X.value,
                np.where(cv_arr <= y_max, XYZClass.Y.value, XYZClass.Z.value),
            ),
        )

        # Safety Stock, Reorder Point, Target DOS, Turnover Ratio, Replenishment Frequency, Peak Multiplier
        z_val = self.config.safety_stock_z_score
        lt_arr = df["lead_time_days"].to_numpy(dtype=np.float64)
        safety_stock = z_val * std_arr * np.sqrt(lt_arr)
        reorder_point = adv_arr * lt_arr + safety_stock

        dos_arr = np.where(
            df["demand_velocity"] == DemandVelocityClass.FAST.value,
            10,
            np.where(df["demand_velocity"] == DemandVelocityClass.MEDIUM.value, 16, 24),
        )
        avg_cycle_inv = np.maximum(1.0, 0.5 * adv_arr * dos_arr + safety_stock)
        turnover_ratio = np.where(adv_arr > 0.0, (adv_arr * 365.0) / avg_cycle_inv, 0.0)
        bounded_cv = np.minimum(np.where(np.isfinite(cv_arr), cv_arr, 2.0), 2.0)
        rep_freq = np.where(adv_arr > 0.0, (30.0 / dos_arr) * (1.0 + 0.15 * bounded_cv), 0.0)
        with np.errstate(divide="ignore", invalid="ignore"):
            peak_mult = np.where(
                adv_arr > 0.0,
                np.maximum(1.0, df["peak_7d_adv"].to_numpy(dtype=np.float64) / adv_arr),
                1.0,
            )

        df["profile_id"] = [format_profile_id(sid, profile_version) for sid in df["sku_id"]]
        df["average_daily_demand"] = np.round(adv_arr, 6)
        df["demand_std_dev"] = np.round(std_arr, 6)
        df["pick_frequency"] = np.round(df["pick_frequency"].to_numpy(dtype=np.float64), 6)
        df["turnover_ratio"] = np.round(turnover_ratio, 4)
        df["safety_stock"] = np.round(safety_stock, 4)
        df["reorder_point"] = np.round(reorder_point, 4)
        df["target_days_of_supply"] = dos_arr.astype(int)
        df["replenishment_frequency"] = np.round(rep_freq, 4)
        df["peak_demand_multiplier"] = np.round(peak_mult, 4)
        df["effective_from"] = effective_from.isoformat()
        df["effective_to"] = None

        # Deterministic baseline on-hand snapshot derived from target stock & demand cycle position
        target_qty = np.maximum(2, np.ceil(adv_arr * dos_arr + safety_stock)).astype(int)
        # Deterministic cycle phase per SKU hash mod 100 so ~22% of active SKUs sit below ROP requiring replenishment
        phase = np.array(
            [(int(sid.split("-")[1]) * 37) % 100 / 100.0 for sid in df["sku_id"]],
            dtype=np.float64,
        )
        on_hand = np.maximum(1, np.round(target_qty * (0.25 + 0.75 * phase))).astype(int)
        df["current_on_hand_qty"] = np.where(zero_mask, 2, on_hand)

        _ = end_dt  # validated horizon bound
        ordered_cols = [
            "profile_id",
            "sku_id",
            "average_daily_demand",
            "demand_std_dev",
            "demand_velocity",
            "velocity_percentile",
            "pick_frequency",
            "total_units_sold",
            "total_order_lines",
            "abc_class",
            "xyz_class",
            "turnover_ratio",
            "safety_stock",
            "reorder_point",
            "target_days_of_supply",
            "current_on_hand_qty",
            "replenishment_frequency",
            "peak_demand_multiplier",
            "effective_from",
            "effective_to",
        ]
        return df[ordered_cols].sort_values("profile_id", kind="mergesort").reset_index(drop=True)

    @staticmethod
    def calculate_single_rop(
        adv: float, lead_time_days: float, demand_std_dev: float, z_score: float = 1.645
    ) -> dict[str, Any]:
        """Pure helper for unit-testing safety stock and reorder point calculations."""
        if adv < 0 or lead_time_days <= 0 or demand_std_dev < 0:
            raise ValueError("Invalid negative demand/std_dev or non-positive lead_time_days")
        safety_stock = round(z_score * demand_std_dev * math.sqrt(lead_time_days), 4)
        reorder_point = round(adv * lead_time_days + safety_stock, 4)
        return {"safety_stock": safety_stock, "reorder_point": reorder_point}
