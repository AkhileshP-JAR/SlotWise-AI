"""Comprehensive 18-rule relational, physical, arithmetic, and leakage-prevention validator.

Implements Section 17 Data Validation requirements using Pandera DataFrame schemas
plus cross-table referential, business-rule, arithmetic, and ground-truth isolation checks.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pandera.pandas as pa

from backend.app.config import DATA_DIR, DomainConfig, load_domain_config
from backend.app.models.enums import (
    ABCClass,
    AccessibilityType,
    AssignmentStatus,
    DemandVelocityClass,
    EntityStatus,
    ExceptionIssueType,
    HazardCode,
    OrderPriority,
    OrderStatus,
    PickEventStatus,
    ReplenishmentPriority,
    ReplenishmentStatus,
    SKUCategory,
    SKUStatus,
    StoragePolicy,
    StorageType,
    TemperatureType,
    WarehouseType,
    XYZClass,
    ZoneType,
)
from backend.app.services.data_generator import OPERATIONAL_TABLE_ORDER
from backend.app.services.demand_analytics import DemandAnalyticsService
from backend.app.utils.ids import ID_PATTERNS

FORBIDDEN_LEAKAGE_COLUMNS: set[str] = {
    "ground_truth",
    "ground_truth_flag",
    "ground_truth_label",
    "is_anomaly",
    "anomaly_id",
    "anomaly_label",
    "expected_issue_type",
    "expected_category",
}


def _build_pandera_schemas() -> dict[str, pa.DataFrameSchema]:
    """Define Pandera structural and domain schemas for core operational tables."""
    return {
        "warehouses": pa.DataFrameSchema(
            {
                "warehouse_id": pa.Column(str, nullable=False),
                "warehouse_name": pa.Column(str, nullable=False),
                "warehouse_type": pa.Column(str, pa.Check.isin([e.value for e in WarehouseType]), nullable=False),
                "total_area_sqft": pa.Column(float, pa.Check.gt(0), nullable=False, coerce=True),
                "daily_labor_minutes_capacity": pa.Column(float, pa.Check.gt(0), nullable=False, coerce=True),
                "status": pa.Column(str, pa.Check.isin([e.value for e in EntityStatus]), nullable=False),
            },
            strict=False,
        ),
        "zones": pa.DataFrameSchema(
            {
                "zone_id": pa.Column(str, nullable=False),
                "warehouse_id": pa.Column(str, nullable=False),
                "zone_type": pa.Column(str, pa.Check.isin([e.value for e in ZoneType]), nullable=False),
                "temperature_type": pa.Column(
                    str, pa.Check.isin([e.value for e in TemperatureType]), nullable=False
                ),
                "max_weight_kg": pa.Column(float, pa.Check.gt(0), nullable=False, coerce=True),
                "max_volume_m3": pa.Column(float, pa.Check.gt(0), nullable=False, coerce=True),
                "status": pa.Column(str, pa.Check.isin([e.value for e in EntityStatus]), nullable=False),
            },
            strict=False,
        ),
        "locations": pa.DataFrameSchema(
            {
                "location_id": pa.Column(str, nullable=False),
                "zone_id": pa.Column(str, nullable=False),
                "aisle_id": pa.Column(str, nullable=False),
                "width_cm": pa.Column(float, pa.Check.gt(0), nullable=False, coerce=True),
                "height_cm": pa.Column(float, pa.Check.gt(0), nullable=False, coerce=True),
                "depth_cm": pa.Column(float, pa.Check.gt(0), nullable=False, coerce=True),
                "max_weight_kg": pa.Column(float, pa.Check.gt(0), nullable=False, coerce=True),
                "max_volume_m3": pa.Column(float, pa.Check.gt(0), nullable=False, coerce=True),
                "current_weight_kg": pa.Column(float, pa.Check.ge(0), nullable=False, coerce=True),
                "current_volume_m3": pa.Column(float, pa.Check.ge(0), nullable=False, coerce=True),
                "accessibility_type": pa.Column(
                    str, pa.Check.isin([e.value for e in AccessibilityType]), nullable=False
                ),
                "storage_policy": pa.Column(str, pa.Check.isin([e.value for e in StoragePolicy]), nullable=False),
                "status": pa.Column(str, pa.Check.isin([e.value for e in EntityStatus]), nullable=False),
            },
            strict=False,
        ),
        "skus": pa.DataFrameSchema(
            {
                "sku_id": pa.Column(str, nullable=False),
                "category": pa.Column(str, pa.Check.isin([e.value for e in SKUCategory]), nullable=False),
                "unit_price": pa.Column(float, pa.Check.gt(0), nullable=False, coerce=True),
                "unit_weight_kg": pa.Column(float, pa.Check.gt(0), nullable=False, coerce=True),
                "width_cm": pa.Column(float, pa.Check.gt(0), nullable=False, coerce=True),
                "height_cm": pa.Column(float, pa.Check.gt(0), nullable=False, coerce=True),
                "depth_cm": pa.Column(float, pa.Check.gt(0), nullable=False, coerce=True),
                "hazard_class": pa.Column(str, pa.Check.isin([e.value for e in HazardCode]), nullable=False),
                "temperature_requirement": pa.Column(
                    str, pa.Check.isin([e.value for e in TemperatureType]), nullable=False
                ),
                "storage_type": pa.Column(str, pa.Check.isin([e.value for e in StorageType]), nullable=False),
                "status": pa.Column(str, pa.Check.isin([e.value for e in SKUStatus]), nullable=False),
            },
            strict=False,
        ),
        "sku_profiles": pa.DataFrameSchema(
            {
                "profile_id": pa.Column(str, nullable=False),
                "sku_id": pa.Column(str, nullable=False),
                "average_daily_demand": pa.Column(float, pa.Check.ge(0), nullable=False, coerce=True),
                "demand_std_dev": pa.Column(float, pa.Check.ge(0), nullable=False, coerce=True),
                "demand_velocity": pa.Column(
                    str, pa.Check.isin([e.value for e in DemandVelocityClass]), nullable=False
                ),
                "abc_class": pa.Column(str, pa.Check.isin([e.value for e in ABCClass]), nullable=False),
                "xyz_class": pa.Column(str, pa.Check.isin([e.value for e in XYZClass]), nullable=False),
            },
            strict=False,
        ),
        "orders": pa.DataFrameSchema(
            {
                "order_id": pa.Column(str, nullable=False),
                "warehouse_id": pa.Column(str, nullable=False),
                "order_priority": pa.Column(str, pa.Check.isin([e.value for e in OrderPriority]), nullable=False),
                "order_status": pa.Column(str, pa.Check.isin([e.value for e in OrderStatus]), nullable=False),
            },
            strict=False,
        ),
        "order_lines": pa.DataFrameSchema(
            {
                "order_line_id": pa.Column(str, nullable=False),
                "order_id": pa.Column(str, nullable=False),
                "sku_id": pa.Column(str, nullable=False),
                "quantity": pa.Column(int, pa.Check.gt(0), nullable=False, coerce=True),
                "unit_price": pa.Column(float, pa.Check.gt(0), nullable=False, coerce=True),
                "line_value": pa.Column(float, pa.Check.gt(0), nullable=False, coerce=True),
            },
            strict=False,
        ),
        "pick_events": pa.DataFrameSchema(
            {
                "pick_event_id": pa.Column(str, nullable=False),
                "order_id": pa.Column(str, nullable=False),
                "order_line_id": pa.Column(str, nullable=False),
                "sku_id": pa.Column(str, nullable=False),
                "location_id": pa.Column(str, nullable=False),
                "picker_id": pa.Column(str, nullable=False),
                "event_status": pa.Column(str, pa.Check.isin([e.value for e in PickEventStatus]), nullable=False),
            },
            strict=False,
        ),
        "replenishments": pa.DataFrameSchema(
            {
                "replenishment_id": pa.Column(str, nullable=False),
                "warehouse_id": pa.Column(str, nullable=False),
                "sku_id": pa.Column(str, nullable=False),
                "destination_location_id": pa.Column(str, nullable=False),
                "required_quantity": pa.Column(int, pa.Check.gt(0), nullable=False, coerce=True),
                "priority": pa.Column(
                    str, pa.Check.isin([e.value for e in ReplenishmentPriority]), nullable=False
                ),
                "replenishment_status": pa.Column(
                    str, pa.Check.isin([e.value for e in ReplenishmentStatus]), nullable=False
                ),
            },
            strict=False,
        ),
        "slot_assignments": pa.DataFrameSchema(
            {
                "slot_assignment_id": pa.Column(str, nullable=False),
                "sku_id": pa.Column(str, nullable=False),
                "location_id": pa.Column(str, nullable=False),
                "warehouse_id": pa.Column(str, nullable=False),
                "assignment_status": pa.Column(
                    str, pa.Check.isin([e.value for e in AssignmentStatus]), nullable=False
                ),
                "assigned_quantity": pa.Column(int, pa.Check.ge(0), nullable=False, coerce=True),
            },
            strict=False,
        ),
    }


class DatasetValidator:
    """Executes all 18 relational, physical, arithmetic, lifecycle, and leakage checks."""

    def __init__(self, config: DomainConfig | None = None, profile: str = "small") -> None:
        self.config = config or load_domain_config(profile)
        self.schemas = _build_pandera_schemas()
        self.demand_service = DemandAnalyticsService(self.config.demand_analytics)

    @staticmethod
    def load_tables_from_disk(
        profile: str, data_root: Path = DATA_DIR
    ) -> tuple[dict[str, pd.DataFrame], pd.DataFrame | None]:
        """Load generated operational CSVs and separated ground-truth CSV from disk."""
        gen_dir = data_root / "generated" / profile
        if not gen_dir.exists():
            raise FileNotFoundError(f"Generated dataset directory not found: {gen_dir}")

        tables: dict[str, pd.DataFrame] = {}
        for name in OPERATIONAL_TABLE_ORDER:
            csv_path = gen_dir / f"{name}.csv"
            if not csv_path.exists():
                raise FileNotFoundError(f"Missing required operational table CSV: {csv_path}")
            tables[name] = pd.read_csv(csv_path, keep_default_na=True)

        gt_path = data_root / "ground_truth" / profile / "ground_truth.csv"
        gt_df = pd.read_csv(gt_path) if gt_path.exists() else None
        return tables, gt_df

    def validate(
        self,
        tables: dict[str, pd.DataFrame],
        ground_truth_df: pd.DataFrame | None = None,
    ) -> dict[str, Any]:
        """Validate operational tables and classify findings into ERROR, WARNING, or EXPECTED_LABELLED_ANOMALY."""
        gt_pairs: set[tuple[str, str]] = set()
        if ground_truth_df is not None and not ground_truth_df.empty:
            for row in ground_truth_df.to_dict(orient="records"):
                gt_pairs.add((str(row["expected_issue_type"]), str(row["entity_id"])))

        matched_gt_pairs: set[tuple[str, str]] = set()
        errors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        expected_anomalies: list[dict[str, Any]] = []
        checks_summary: dict[str, str] = {}

        def _record_finding(
            check_name: str,
            default_level: str,
            entity_type: str,
            entity_id: str,
            message: str,
            issue_type: str | None = None,
        ) -> None:
            if issue_type and (issue_type, entity_id) in gt_pairs:
                matched_gt_pairs.add((issue_type, entity_id))
                expected_anomalies.append(
                    {
                        "classification": "EXPECTED_LABELLED_ANOMALY",
                        "check": check_name,
                        "issue_type": issue_type,
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "message": message,
                    }
                )
                return

            payload = {
                "classification": default_level,
                "check": check_name,
                "issue_type": issue_type or check_name,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "message": message,
            }
            if default_level == "ERROR":
                errors.append(payload)
            else:
                warnings.append(payload)

        # Check 0: Required tables present
        missing_tables = [t for t in OPERATIONAL_TABLE_ORDER if t not in tables]
        if missing_tables:
            for mt in missing_tables:
                _record_finding("REQUIRED_TABLES", "ERROR", "TABLE", mt, f"Missing operational table {mt}")
            return self._finalize_report(
                tables, checks_summary, errors, warnings, expected_anomalies, gt_pairs, matched_gt_pairs
            )

        # Check 18 (run upfront): GROUND_TRUTH_SEPARATION
        leak_count = 0
        for t_name, df in tables.items():
            leaked = FORBIDDEN_LEAKAGE_COLUMNS.intersection({c.lower() for c in df.columns})
            if leaked:
                leak_count += 1
                _record_finding(
                    "GROUND_TRUTH_SEPARATION",
                    "ERROR",
                    "TABLE",
                    t_name,
                    f"Operational table '{t_name}' contains forbidden ground-truth columns: {sorted(leaked)}",
                )
        checks_summary["18_GROUND_TRUTH_SEPARATION"] = "PASS" if leak_count == 0 else "FAIL"

        # Check 1: PK_UNIQUENESS
        pk_defs: dict[str, list[str]] = {
            "warehouses": ["warehouse_id"],
            "hazard_classes": ["hazard_class_id"],
            "zones": ["zone_id"],
            "zone_hazard_allowances": ["zone_id", "hazard_class_id"],
            "compatibility_rules": ["rule_id"],
            "aisles": ["aisle_id"],
            "locations": ["location_id"],
            "suppliers": ["supplier_id"],
            "skus": ["sku_id"],
            "sku_profiles": ["profile_id"],
            "orders": ["order_id"],
            "order_lines": ["order_line_id"],
            "pickers": ["picker_id"],
            "pick_events": ["pick_event_id"],
            "replenishments": ["replenishment_id"],
            "slot_assignments": ["slot_assignment_id"],
        }
        pk_err = 0
        for t_name, pk_cols in pk_defs.items():
            df = tables[t_name]
            if df.empty:
                continue
            if df[pk_cols].isna().any().any():
                pk_err += 1
                _record_finding("PK_UNIQUENESS", "ERROR", "TABLE", t_name, f"Null primary key in {t_name}")
            dup_mask = df.duplicated(subset=pk_cols, keep=False)
            if dup_mask.any():
                pk_err += int(dup_mask.sum())
                sample_id = str(df.loc[dup_mask, pk_cols[0]].iloc[0])
                _record_finding(
                    "PK_UNIQUENESS",
                    "ERROR",
                    "TABLE",
                    sample_id,
                    f"Duplicate primary key detected in {t_name} ({int(dup_mask.sum())} rows).",
                )
        checks_summary["01_PK_UNIQUENESS"] = "PASS" if pk_err == 0 else "FAIL"

        # Check 2: FK_INTEGRITY
        fk_specs = [
            ("zones", "warehouse_id", "warehouses", "warehouse_id", False),
            ("zone_hazard_allowances", "zone_id", "zones", "zone_id", False),
            ("zone_hazard_allowances", "hazard_class_id", "hazard_classes", "hazard_class_id", False),
            ("aisles", "zone_id", "zones", "zone_id", False),
            ("aisles", "warehouse_id", "warehouses", "warehouse_id", False),
            ("locations", "zone_id", "zones", "zone_id", False),
            ("locations", "aisle_id", "aisles", "aisle_id", False),
            ("skus", "hazard_class", "hazard_classes", "hazard_class_id", False),
            ("skus", "supplier_id", "suppliers", "supplier_id", True),
            ("sku_profiles", "sku_id", "skus", "sku_id", False),
            ("orders", "warehouse_id", "warehouses", "warehouse_id", False),
            ("order_lines", "order_id", "orders", "order_id", False),
            ("order_lines", "sku_id", "skus", "sku_id", False),
            ("pickers", "warehouse_id", "warehouses", "warehouse_id", False),
            ("pick_events", "order_id", "orders", "order_id", False),
            ("pick_events", "order_line_id", "order_lines", "order_line_id", False),
            ("pick_events", "sku_id", "skus", "sku_id", False),
            ("pick_events", "location_id", "locations", "location_id", False),
            ("pick_events", "picker_id", "pickers", "picker_id", False),
            ("replenishments", "warehouse_id", "warehouses", "warehouse_id", False),
            ("replenishments", "sku_id", "skus", "sku_id", False),
            ("replenishments", "source_location_id", "locations", "location_id", True),
            ("replenishments", "destination_location_id", "locations", "location_id", False),
            ("slot_assignments", "sku_id", "skus", "sku_id", False),
            ("slot_assignments", "location_id", "locations", "location_id", False),
            ("slot_assignments", "warehouse_id", "warehouses", "warehouse_id", False),
        ]
        fk_err = 0
        for child_tbl, child_col, parent_tbl, parent_col, nullable in fk_specs:
            c_df = tables[child_tbl]
            if c_df.empty:
                continue
            parent_ids = set(tables[parent_tbl][parent_col].dropna().astype(str).tolist())
            series = c_df[child_col]
            if not nullable and series.isna().any():
                fk_err += 1
                _record_finding(
                    "FK_INTEGRITY",
                    "ERROR",
                    child_tbl.upper(),
                    child_col,
                    f"Non-nullable FK {child_tbl}.{child_col} contains NULL values.",
                    issue_type=ExceptionIssueType.ORPHAN_FOREIGN_KEY.value,
                )
            non_null = series.dropna().astype(str)
            orphans = non_null[~non_null.isin(parent_ids)]
            if not orphans.empty:
                fk_err += len(orphans)
                _record_finding(
                    "FK_INTEGRITY",
                    "ERROR",
                    child_tbl.upper(),
                    str(orphans.iloc[0]),
                    f"{len(orphans)} orphan FK values in {child_tbl}.{child_col} -> {parent_tbl}.{parent_col}",
                    issue_type=ExceptionIssueType.ORPHAN_FOREIGN_KEY.value,
                )
        checks_summary["02_FK_INTEGRITY"] = "PASS" if fk_err == 0 else "FAIL"

        # Check 3: ID_CONVENTION_FORMAT
        id_checks = [
            ("warehouses", "warehouse_id", "warehouse_id"),
            ("zones", "zone_id", "zone_id"),
            ("aisles", "aisle_id", "aisle_id"),
            ("locations", "location_id", "location_id"),
            ("suppliers", "supplier_id", "supplier_id"),
            ("skus", "sku_id", "sku_id"),
            ("sku_profiles", "profile_id", "profile_id"),
            ("orders", "order_id", "order_id"),
            ("order_lines", "order_line_id", "order_line_id"),
            ("pickers", "picker_id", "picker_id"),
            ("pick_events", "pick_event_id", "pick_event_id"),
            ("replenishments", "replenishment_id", "replenishment_id"),
            ("slot_assignments", "slot_assignment_id", "slot_assignment_id"),
            ("compatibility_rules", "rule_id", "rule_id"),
        ]
        id_err = 0
        for tbl, col, pat_key in id_checks:
            df = tables[tbl]
            if df.empty:
                continue
            regex = ID_PATTERNS[pat_key]
            bad = df[~df[col].astype(str).str.match(regex)]
            if not bad.empty:
                id_err += len(bad)
                _record_finding(
                    "ID_CONVENTION_FORMAT",
                    "ERROR",
                    tbl.upper(),
                    str(bad[col].iloc[0]),
                    f"{len(bad)} IDs in {tbl}.{col} violate pattern {regex.pattern}",
                )
        checks_summary["03_ID_CONVENTION_FORMAT"] = "PASS" if id_err == 0 else "FAIL"

        # Checks 4, 5, 6, 7: Pandera schema, enum domain, nullability, dimension & weight positivity
        schema_err = 0
        for tbl_name, schema in self.schemas.items():
            df = tables[tbl_name]
            if df.empty:
                continue
            try:
                schema.validate(df, lazy=True)
            except pa.errors.SchemaErrors as exc:
                schema_err += len(exc.failure_cases)
                first_msg = str(exc.failure_cases.iloc[0].to_dict())
                _record_finding(
                    "PANDERA_SCHEMA_DOMAIN",
                    "ERROR",
                    tbl_name.upper(),
                    tbl_name,
                    f"Pandera validation failed on {tbl_name}: {first_msg}",
                )
        status_str = "PASS" if schema_err == 0 else "FAIL"
        checks_summary["04_ENUM_DOMAIN_VALIDITY"] = status_str
        checks_summary["05_REQUIRED_NON_NULL"] = status_str
        checks_summary["06_DIMENSION_POSITIVITY"] = status_str
        checks_summary["07_WEIGHT_POSITIVITY"] = status_str

        # Check 8: VOLUME_CONSISTENCY
        locs = tables["locations"]
        calc_vol = (locs["width_cm"] * locs["height_cm"] * locs["depth_cm"]) / 1_000_000.0
        vol_diff = (locs["max_volume_m3"] - calc_vol).abs()
        bad_vol = locs[vol_diff > 1e-4]
        for _, r in bad_vol.iterrows():
            _record_finding(
                "VOLUME_CONSISTENCY",
                "ERROR",
                "LOCATION",
                str(r["location_id"]),
                f"Location max_volume_m3 ({r['max_volume_m3']}) != W*H*D/1e6 ({(r['width_cm'] * r['height_cm'] * r['depth_cm']) / 1e6:.6f})",
            )
        checks_summary["08_VOLUME_CONSISTENCY"] = "PASS" if bad_vol.empty else "FAIL"

        # Check 9: CAPACITY_CONSTRAINTS & near-exhaustion warnings
        near_thr = self.config.exceptions.capacity_near_exhaustion_ratio
        over_cap = locs[
            (locs["current_weight_kg"] > locs["max_weight_kg"] + 1e-5)
            | (locs["current_volume_m3"] > locs["max_volume_m3"] + 1e-6)
        ]
        for _, r in over_cap.iterrows():
            _record_finding(
                "CAPACITY_CONSTRAINTS",
                "ERROR",
                "LOCATION",
                str(r["location_id"]),
                f"Location {r['location_id']} exceeds capacity (wt {r['current_weight_kg']}/{r['max_weight_kg']}, vol {r['current_volume_m3']}/{r['max_volume_m3']}).",
                issue_type=ExceptionIssueType.CAPACITY_EXCEEDED.value,
            )

        within_cap = locs.drop(over_cap.index)
        wt_ratio = within_cap["current_weight_kg"] / within_cap["max_weight_kg"]
        vol_ratio = within_cap["current_volume_m3"] / within_cap["max_volume_m3"]
        near_cap = within_cap[(wt_ratio >= near_thr) | (vol_ratio >= near_thr)]
        for _, r in near_cap.iterrows():
            _record_finding(
                "CAPACITY_CONSTRAINTS",
                "WARNING",
                "LOCATION",
                str(r["location_id"]),
                f"Location {r['location_id']} utilization >= {near_thr * 100:.0f}% warning threshold.",
                issue_type=ExceptionIssueType.CAPACITY_NEARLY_EXHAUSTED.value,
            )
        checks_summary["09_CAPACITY_CONSTRAINTS"] = (
            "PASS"
            if all(
                (ExceptionIssueType.CAPACITY_EXCEEDED.value, str(r["location_id"])) in gt_pairs
                for _, r in over_cap.iterrows()
            )
            else "FAIL"
        )

        # Check 10: TEMPORAL_DATE_ORDERING
        date_err = 0
        profs = tables["sku_profiles"]
        has_eff_to = profs[profs["effective_to"].notna()]
        if not has_eff_to.empty:
            bad_p = has_eff_to[
                pd.to_datetime(has_eff_to["effective_from"]) > pd.to_datetime(has_eff_to["effective_to"])
            ]
            date_err += len(bad_p)
        picks = tables["pick_events"]
        orders = tables["orders"]
        if not picks.empty and not orders.empty:
            o_dt = pd.to_datetime(orders.set_index("order_id")["order_date"]).to_dict()
            p_ord_dt = picks["order_id"].map(o_dt)
            bad_pick_dt = picks[pd.to_datetime(picks["pick_timestamp"]) < p_ord_dt]
            if not bad_pick_dt.empty:
                date_err += len(bad_pick_dt)
                _record_finding(
                    "TEMPORAL_DATE_ORDERING",
                    "ERROR",
                    "PICK_EVENT",
                    str(bad_pick_dt["pick_event_id"].iloc[0]),
                    f"{len(bad_pick_dt)} pick events have pick_timestamp earlier than order_date.",
                )
        checks_summary["10_TEMPORAL_DATE_ORDERING"] = "PASS" if date_err == 0 else "FAIL"

        # Check 11: LIFECYCLE_TRANSITIONS
        assns = tables["slot_assignments"]
        lc_err = 0
        bad_active_eff = assns[
            (assns["assignment_status"] == AssignmentStatus.ACTIVE.value) & (assns["effective_to"].notna())
        ]
        if not bad_active_eff.empty:
            lc_err += len(bad_active_eff)
            _record_finding(
                "LIFECYCLE_TRANSITIONS",
                "ERROR",
                "SLOT_ASSIGNMENT",
                str(bad_active_eff["slot_assignment_id"].iloc[0]),
                "ACTIVE slot assignment cannot have a closed effective_to timestamp.",
                issue_type=ExceptionIssueType.INVALID_LIFECYCLE_TRANSITION.value,
            )
        checks_summary["11_LIFECYCLE_TRANSITIONS"] = "PASS" if lc_err == 0 else "FAIL"

        # Check 12: DUPLICATE_ACTIVE_ASSIGNMENTS on DEDICATED_SINGLE_SKU locations
        active_assns = assns[assns["assignment_status"] == AssignmentStatus.ACTIVE.value]
        loc_policy_map = locs.set_index("location_id")["storage_policy"].to_dict()
        loc_active_counts = active_assns.groupby("location_id")["slot_assignment_id"].count()
        dup_unexp = 0
        for lid, cnt in loc_active_counts.items():
            if cnt > 1 and loc_policy_map.get(lid) == StoragePolicy.DEDICATED_SINGLE_SKU.value:
                if (ExceptionIssueType.DUPLICATE_ACTIVE_ASSIGNMENT.value, str(lid)) not in gt_pairs:
                    dup_unexp += 1
                _record_finding(
                    "DUPLICATE_ACTIVE_ASSIGNMENTS",
                    "ERROR",
                    "LOCATION",
                    str(lid),
                    f"DEDICATED_SINGLE_SKU location {lid} holds {cnt} ACTIVE assignments.",
                    issue_type=ExceptionIssueType.DUPLICATE_ACTIVE_ASSIGNMENT.value,
                )
        checks_summary["12_DUPLICATE_ACTIVE_ASSIGNMENTS"] = "PASS" if dup_unexp == 0 else "FAIL"

        # Check 13: WAREHOUSE_ZONE_CONSISTENCY
        zone_wh = tables["zones"].set_index("zone_id")["warehouse_id"].to_dict()
        loc_wh = {
            str(r["location_id"]): zone_wh.get(str(r["zone_id"]))
            for _, r in locs[["location_id", "zone_id"]].iterrows()
        }
        wz_err = 0
        for _, r in assns.iterrows():
            expected_wh = loc_wh.get(str(r["location_id"]))
            if expected_wh != str(r["warehouse_id"]):
                wz_err += 1
                _record_finding(
                    "WAREHOUSE_ZONE_CONSISTENCY",
                    "ERROR",
                    "SLOT_ASSIGNMENT",
                    str(r["slot_assignment_id"]),
                    f"SlotAssignment warehouse {r['warehouse_id']} != location warehouse {expected_wh}.",
                )
        checks_summary["13_WAREHOUSE_ZONE_CONSISTENCY"] = "PASS" if wz_err == 0 else "FAIL"

        # Check 14: SKU_PROFILE_CONSISTENCY (recompute ADV & ROP from source transactions)
        prof_err = 0
        skus = tables["skus"]
        missing_profs = set(skus["sku_id"].tolist()) - set(profs["sku_id"].tolist())
        for m_sid in sorted(missing_profs):
            prof_err += 1
            _record_finding(
                "SKU_PROFILE_CONSISTENCY",
                "ERROR",
                "SKU",
                m_sid,
                f"SKU {m_sid} is missing an analytical SKUProfile.",
                issue_type=ExceptionIssueType.MISSING_SKU_PROFILE.value,
            )

        recomputed = self.demand_service.compute_profiles_dataframe(
            skus_df=skus,
            orders_df=orders,
            order_lines_df=tables["order_lines"],
            suppliers_df=tables["suppliers"],
            observation_start_date=self.config.dataset.observation_start_date,
            observation_days=self.config.dataset.observation_days,
            effective_from=datetime(2026, 4, 1, 0, 0, 0),
        )
        merged_prof = profs.merge(
            recomputed[["sku_id", "average_daily_demand", "abc_class", "xyz_class", "reorder_point"]],
            on="sku_id",
            suffixes=("_actual", "_expected"),
        )
        drift = merged_prof[
            (
                (merged_prof["average_daily_demand_actual"] - merged_prof["average_daily_demand_expected"]).abs()
                > 1e-4
            )
            | (merged_prof["abc_class_actual"] != merged_prof["abc_class_expected"])
            | (merged_prof["xyz_class_actual"] != merged_prof["xyz_class_expected"])
        ]
        if not drift.empty:
            prof_err += len(drift)
            _record_finding(
                "SKU_PROFILE_CONSISTENCY",
                "ERROR",
                "SKU_PROFILE",
                str(drift["profile_id"].iloc[0]),
                f"{len(drift)} SKU profiles drifted from transactional order_lines derivation.",
            )
        checks_summary["14_SKU_PROFILE_CONSISTENCY"] = "PASS" if prof_err == 0 else "FAIL"

        # Check 15: ORDER_LINE_ARITHMETIC
        olines = tables["order_lines"]
        expected_val = np.round(olines["quantity"] * olines["unit_price"], 2)
        bad_math = olines[(olines["line_value"] - expected_val).abs() > 0.011]
        if not bad_math.empty:
            _record_finding(
                "ORDER_LINE_ARITHMETIC",
                "ERROR",
                "ORDER_LINE",
                str(bad_math["order_line_id"].iloc[0]),
                f"{len(bad_math)} order lines fail line_value == round(quantity * unit_price, 2).",
            )
        checks_summary["15_ORDER_LINE_ARITHMETIC"] = "PASS" if bad_math.empty else "FAIL"

        # Check 16: PICK_EVENT_RECONCILIATION (quantity bounds & blocked-aisle traversal)
        aisle_blocked_map = tables["aisles"].set_index("aisle_id")["is_blocked"].to_dict()
        loc_aisle_map = locs.set_index("location_id")["aisle_id"].to_dict()
        ol_qty_map = olines.set_index("order_line_id")["quantity"].to_dict()
        pick_unexp = 0
        for _, pr in picks.iterrows():
            pe_id = str(pr["pick_event_id"])
            max_q = ol_qty_map.get(str(pr["order_line_id"]), 0)
            if int(pr["quantity_picked"]) > int(max_q):
                pick_unexp += 1
                _record_finding(
                    "PICK_EVENT_RECONCILIATION",
                    "ERROR",
                    "PICK_EVENT",
                    pe_id,
                    f"Pick event {pe_id} picked {pr['quantity_picked']} > ordered {max_q}.",
                )
            aid = loc_aisle_map.get(str(pr["location_id"]))
            if aid and bool(aisle_blocked_map.get(aid, False)):
                if (ExceptionIssueType.BLOCKED_AISLE_PICK_PATH.value, pe_id) not in gt_pairs:
                    pick_unexp += 1
                _record_finding(
                    "PICK_EVENT_RECONCILIATION",
                    "ERROR",
                    "PICK_EVENT",
                    pe_id,
                    f"Pick event {pe_id} traverses blocked aisle {aid}.",
                    issue_type=ExceptionIssueType.BLOCKED_AISLE_PICK_PATH.value,
                )
        checks_summary["16_PICK_EVENT_RECONCILIATION"] = "PASS" if pick_unexp == 0 else "FAIL"

        # Check 17: REPLENISHMENT_RECONCILIATION (warehouse daily labor capacity vs required labor)
        reps = tables["replenishments"]
        wh_caps = tables["warehouses"].set_index("warehouse_id")["daily_labor_minutes_capacity"].to_dict()
        rep_unexp = 0
        if not reps.empty:
            active_reps = reps[
                reps["replenishment_status"].isin(
                    [
                        ReplenishmentStatus.PENDING.value,
                        ReplenishmentStatus.IN_PROGRESS.value,
                        ReplenishmentStatus.OVERLOADED.value,
                    ]
                )
            ]
            wh_req = active_reps.groupby("warehouse_id")["labor_minutes_required"].sum().to_dict()
            for wid, req_min in wh_req.items():
                cap_min = float(wh_caps.get(wid, 0.0))
                if req_min > cap_min:
                    if (ExceptionIssueType.REPLENISHMENT_LABOR_OVERLOAD.value, str(wid)) not in gt_pairs:
                        rep_unexp += 1
                    _record_finding(
                        "REPLENISHMENT_RECONCILIATION",
                        "WARNING",
                        "WAREHOUSE",
                        str(wid),
                        f"Warehouse {wid} replenishment labor ({req_min:.1f}m) exceeds capacity ({cap_min:.1f}m).",
                        issue_type=ExceptionIssueType.REPLENISHMENT_LABOR_OVERLOAD.value,
                    )
        checks_summary["17_REPLENISHMENT_RECONCILIATION"] = "PASS" if rep_unexp == 0 else "FAIL"

        # Additional Business Compatibility Check (Co-location conflicts & Oversized unassignable SKUs)
        self._check_compatibility_and_feasibility(tables, gt_pairs, _record_finding)

        return self._finalize_report(
            tables, checks_summary, errors, warnings, expected_anomalies, gt_pairs, matched_gt_pairs
        )

    def _check_compatibility_and_feasibility(
        self,
        tables: dict[str, pd.DataFrame],
        gt_pairs: set[tuple[str, str]],
        record_fn: Any,
    ) -> None:
        skus = tables["skus"]
        locs = tables["locations"]
        assns = tables["slot_assignments"]
        rules = tables["compatibility_rules"]

        sku_map = skus.set_index("sku_id").to_dict(orient="index")
        active_assns = assns[assns["assignment_status"] == AssignmentStatus.ACTIVE.value]

        # Build pairwise conflict sets from compatibility_rules
        hazard_conflicts: set[tuple[str, str]] = set()
        cat_conflicts: set[tuple[str, str]] = set()
        for r in rules.to_dict(orient="records"):
            a, b = str(r["primary_attribute"]), str(r["conflicting_attribute"])
            if r["rule_type"] == "HAZARD_CO_LOCATION":
                hazard_conflicts.update({(a, b), (b, a)})
            elif r["rule_type"] == "CATEGORY_CO_LOCATION":
                cat_conflicts.update({(a, b), (b, a)})

        # Check co-located SKUs in shared locations
        grouped_locs = active_assns.groupby("location_id")["sku_id"].apply(list)
        for lid, s_list in grouped_locs.items():
            if len(s_list) < 2:
                continue
            conflict_found = False
            for i in range(len(s_list)):
                for j in range(i + 1, len(s_list)):
                    s1, s2 = sku_map[s_list[i]], sku_map[s_list[j]]
                    if (s1["hazard_class"], s2["hazard_class"]) in hazard_conflicts or (
                        s1["category"],
                        s2["category"],
                    ) in cat_conflicts:
                        conflict_found = True
                        break
                if conflict_found:
                    break
            if conflict_found:
                record_fn(
                    "ASSIGNMENT_COMPATIBILITY",
                    "ERROR",
                    "LOCATION",
                    str(lid),
                    f"Incompatible SKUs co-located in location {lid}: {s_list}",
                    issue_type=ExceptionIssueType.INCOMPATIBLE_COLOCATION.value,
                )

        # Check SKUs exceeding maximum physical slot dimensions across entire warehouse network
        max_w = float(locs["width_cm"].max())
        max_h = float(locs["height_cm"].max())
        max_d = float(locs["depth_cm"].max())
        oversized = skus[(skus["width_cm"] > max_w) | (skus["height_cm"] > max_h) | (skus["depth_cm"] > max_d)]
        for _, sr in oversized.iterrows():
            sid = str(sr["sku_id"])
            record_fn(
                "ASSIGNMENT_COMPATIBILITY",
                "WARNING",
                "SKU",
                sid,
                f"SKU {sid} dimensions ({sr['width_cm']}x{sr['height_cm']}x{sr['depth_cm']}) exceed all facility locations.",
                issue_type=ExceptionIssueType.NO_COMPATIBLE_LOCATION.value,
            )
        _ = gt_pairs

    def _finalize_report(
        self,
        tables: dict[str, pd.DataFrame],
        checks_summary: dict[str, str],
        errors: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
        expected_anomalies: list[dict[str, Any]],
        gt_pairs: set[tuple[str, str]],
        matched_gt_pairs: set[tuple[str, str]],
    ) -> dict[str, Any]:
        unmatched_gt = sorted(
            [{"issue_type": k[0], "entity_id": k[1]} for k in (gt_pairs - matched_gt_pairs)],
            key=lambda x: (x["issue_type"], x["entity_id"]),
        )
        is_valid = len(errors) == 0 and len(unmatched_gt) == 0
        return {
            "profile": self.config.dataset.profile,
            "is_valid": is_valid,
            "entity_counts": {k: int(len(v)) for k, v in tables.items()},
            "summary": {
                "unexpected_error_count": len(errors),
                "operational_warning_count": len(warnings),
                "expected_labelled_anomaly_count": len(expected_anomalies),
                "ground_truth_expected_total": len(gt_pairs),
                "ground_truth_matched_total": len(matched_gt_pairs),
                "ground_truth_unmatched_total": len(unmatched_gt),
            },
            "checks": dict(sorted(checks_summary.items())),
            "errors": errors,
            "warnings": warnings,
            "expected_labelled_anomalies": expected_anomalies,
            "unmatched_ground_truth": unmatched_gt,
        }

    @staticmethod
    def save_report(report: dict[str, Any], output_root: Path = DATA_DIR) -> Path:
        man_dir = output_root / "manifests"
        man_dir.mkdir(parents=True, exist_ok=True)
        out_path = man_dir / f"{report['profile']}_validation_report.json"
        out_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return out_path
