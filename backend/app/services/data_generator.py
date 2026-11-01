"""Deterministic, configurable synthetic warehouse dataset generator and anomaly injector.

Supports SMALL, MEDIUM, and FULL dataset profiles from a single parameterized engine
using fixed seed 20261022. Generates rule-compliant baseline operational records first,
then injects isolated, explicitly documented anomalies and exports ground-truth labels
strictly to separate ground-truth files (Section 7 & Section 10).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backend.app.config import DATA_DIR, DomainConfig, load_domain_config
from backend.app.models.enums import (
    AccessibilityType,
    AssignmentStatus,
    EntityStatus,
    ExceptionCategory,
    ExceptionIssueType,
    ExceptionSeverity,
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
    ZoneType,
)
from backend.app.services.demand_analytics import DemandAnalyticsService
from backend.app.utils.ids import (
    compute_file_sha256,
    format_aisle_id,
    format_location_id,
    format_order_id,
    format_order_line_id,
    format_pick_event_id,
    format_picker_id,
    format_replenishment_id,
    format_rule_id,
    format_sku_id,
    format_slot_assignment_id,
    format_supplier_id,
    format_warehouse_id,
    format_zone_id,
)

OPERATIONAL_TABLE_ORDER: list[str] = [
    "warehouses",
    "hazard_classes",
    "zones",
    "zone_hazard_allowances",
    "compatibility_rules",
    "aisles",
    "locations",
    "suppliers",
    "skus",
    "sku_profiles",
    "orders",
    "order_lines",
    "pickers",
    "pick_events",
    "replenishments",
    "slot_assignments",
]

CITY_CATALOG: list[tuple[str, str, str]] = [
    ("Pune", "Maharashtra", "India"),
    ("Bengaluru", "Karnataka", "India"),
    ("Hyderabad", "Telangana", "India"),
    ("Mumbai", "Maharashtra", "India"),
    ("Chennai", "Tamil Nadu", "India"),
    ("Delhi NCR", "Haryana", "India"),
    ("Ahmedabad", "Gujarat", "India"),
    ("Kolkata", "West Bengal", "India"),
    ("Nagpur", "Maharashtra", "India"),
    ("Coimbatore", "Tamil Nadu", "India"),
    ("Indore", "Madhya Pradesh", "India"),
    ("Jaipur", "Rajasthan", "India"),
    ("Lucknow", "Uttar Pradesh", "India"),
    ("Surat", "Gujarat", "India"),
    ("Visakhapatnam", "Andhra Pradesh", "India"),
    ("Kochi", "Kerala", "India"),
    ("Bhubaneswar", "Odisha", "India"),
    ("Chandigarh", "Punjab", "India"),
    ("Vadodara", "Gujarat", "India"),
    ("Guwahati", "Assam", "India"),
]

ZONE_TEMPLATES: list[dict[str, Any]] = [
    {
        "suffix": "Forward Fast Pick",
        "zone_type": ZoneType.FAST_PICK.value,
        "temperature_type": TemperatureType.AMBIENT.value,
        "hazards": [HazardCode.NONE.value, HazardCode.LITHIUM_BATTERY.value],
        "share": 0.24,
        "x_offset": 6.0,
        "dims": (70.0, 105.0, 55.0, 85.0, 70.0, 100.0),
        "weight_range": (120.0, 260.0),
    },
    {
        "suffix": "General Ambient Storage",
        "zone_type": ZoneType.GENERAL.value,
        "temperature_type": TemperatureType.AMBIENT.value,
        "hazards": [HazardCode.NONE.value, HazardCode.LITHIUM_BATTERY.value],
        "share": 0.26,
        "x_offset": 22.0,
        "dims": (90.0, 130.0, 75.0, 115.0, 85.0, 120.0),
        "weight_range": (220.0, 500.0),
    },
    {
        "suffix": "Bulk & Oversize Bay",
        "zone_type": ZoneType.OVERSIZE.value,
        "temperature_type": TemperatureType.AMBIENT.value,
        "hazards": [HazardCode.NONE.value],
        "share": 0.14,
        "x_offset": 40.0,
        "dims": (160.0, 240.0, 150.0, 220.0, 140.0, 200.0),
        "weight_range": (900.0, 2200.0),
    },
    {
        "suffix": "Chilled Cold Chain",
        "zone_type": ZoneType.COLD_CHAIN.value,
        "temperature_type": TemperatureType.CHILLED.value,
        "hazards": [HazardCode.NONE.value],
        "share": 0.11,
        "x_offset": 26.0,
        "dims": (85.0, 125.0, 75.0, 110.0, 85.0, 115.0),
        "weight_range": (200.0, 480.0),
    },
    {
        "suffix": "Deep Freeze Vault",
        "zone_type": ZoneType.COLD_CHAIN.value,
        "temperature_type": TemperatureType.FROZEN.value,
        "hazards": [HazardCode.NONE.value],
        "share": 0.08,
        "x_offset": 34.0,
        "dims": (85.0, 125.0, 75.0, 110.0, 85.0, 115.0),
        "weight_range": (200.0, 480.0),
    },
    {
        "suffix": "Controlled Pharma Vault",
        "zone_type": ZoneType.GENERAL.value,
        "temperature_type": TemperatureType.CONTROLLED_ROOM.value,
        "hazards": [HazardCode.NONE.value],
        "share": 0.08,
        "x_offset": 16.0,
        "dims": (75.0, 110.0, 65.0, 95.0, 75.0, 105.0),
        "weight_range": (150.0, 350.0),
    },
    {
        "suffix": "Segregated Hazmat Containment",
        "zone_type": ZoneType.HAZMAT.value,
        "temperature_type": TemperatureType.AMBIENT.value,
        "hazards": [
            HazardCode.NONE.value,
            HazardCode.FLAMMABLE.value,
            HazardCode.CORROSIVE.value,
            HazardCode.OXIDIZER.value,
            HazardCode.TOXIC.value,
            HazardCode.LITHIUM_BATTERY.value,
        ],
        "share": 0.09,
        "x_offset": 52.0,
        "dims": (95.0, 135.0, 85.0, 125.0, 90.0, 125.0),
        "weight_range": (260.0, 650.0),
    },
]


def _allocate_exact_counts(total: int, weights: np.ndarray, min_each: int = 1) -> list[int]:
    """Deterministically partition `total` integer units across buckets proportionally to `weights`."""
    n = len(weights)
    if total < n * min_each:
        raise ValueError(f"Cannot allocate total={total} across {n} buckets with min_each={min_each}")
    remaining = total - n * min_each
    norm_w = weights / float(np.sum(weights))
    raw = norm_w * remaining
    floors = np.floor(raw).astype(int)
    diff = int(remaining - int(np.sum(floors)))
    if diff > 0:
        remainders = raw - floors
        # Deterministic tie-break by index
        order = np.lexsort((np.arange(n), -remainders))
        for idx in order[:diff]:
            floors[idx] += 1
    return (floors + min_each).tolist()


@dataclass
class GeneratedDatasetBundle:
    """In-memory container of generated operational tables, ground-truth labels, and manifest."""

    profile: str
    seed: int
    tables: dict[str, pd.DataFrame]
    ground_truth: pd.DataFrame
    manifest: dict[str, Any]


class SyntheticDataGenerator:
    """Deterministic multi-profile warehouse slotting dataset generator."""

    def __init__(
        self, config: DomainConfig | None = None, profile: str = "small", seed: int | None = None
    ) -> None:
        self.config = config or load_domain_config(profile)
        self.profile = self.config.dataset.profile
        self.seed = seed if seed is not None else self.config.project.seed
        self.year = self.config.dataset.reference_year
        self.obs_start = datetime.fromisoformat(f"{self.config.dataset.observation_start_date}T00:00:00")
        self.obs_days = self.config.dataset.observation_days
        self.snapshot_dt = self.obs_start + timedelta(days=self.obs_days)
        self.demand_service = DemandAnalyticsService(self.config.demand_analytics)

    def generate(self) -> GeneratedDatasetBundle:
        """Generate clean baseline tables, derive profiles, and inject isolated labelled anomalies."""
        rng = np.random.Generator(np.random.PCG64(self.seed))

        hazard_df = self._build_hazard_classes()
        rules_df = self._build_compatibility_rules()
        suppliers_df = self._build_suppliers(rng)
        warehouses_df, zones_df, bridge_df, aisles_df, locations_df, pickers_df = self._build_warehouse_topology(
            rng
        )
        skus_df, sku_home_wh = self._build_skus(rng, suppliers_df, warehouses_df)
        orders_df, order_lines_df = self._build_orders_and_lines(rng, warehouses_df, skus_df, sku_home_wh)

        sku_profiles_df = self.demand_service.compute_profiles_dataframe(
            skus_df=skus_df,
            orders_df=orders_df,
            order_lines_df=order_lines_df,
            suppliers_df=suppliers_df,
            observation_start_date=self.config.dataset.observation_start_date,
            observation_days=self.obs_days,
            effective_from=self.snapshot_dt,
            profile_version=1,
        )

        slot_assignments_df, locations_df = self._build_baseline_assignments(
            rng=rng,
            skus_df=skus_df,
            sku_profiles_df=sku_profiles_df,
            sku_home_wh=sku_home_wh,
            zones_df=zones_df,
            bridge_df=bridge_df,
            locations_df=locations_df,
        )

        pick_events_df = self._build_pick_events(
            rng=rng,
            orders_df=orders_df,
            order_lines_df=order_lines_df,
            slot_assignments_df=slot_assignments_df,
            locations_df=locations_df,
            warehouses_df=warehouses_df,
            pickers_df=pickers_df,
        )

        replenishments_df = self._build_baseline_replenishments(
            warehouses_df=warehouses_df,
            zones_df=zones_df,
            locations_df=locations_df,
            skus_df=skus_df,
            sku_profiles_df=sku_profiles_df,
            slot_assignments_df=slot_assignments_df,
        )

        tables: dict[str, pd.DataFrame] = {
            "warehouses": warehouses_df,
            "hazard_classes": hazard_df,
            "zones": zones_df,
            "zone_hazard_allowances": bridge_df,
            "compatibility_rules": rules_df,
            "aisles": aisles_df,
            "locations": locations_df,
            "suppliers": suppliers_df,
            "skus": skus_df,
            "sku_profiles": sku_profiles_df,
            "orders": orders_df,
            "order_lines": order_lines_df,
            "pickers": pickers_df,
            "pick_events": pick_events_df,
            "replenishments": replenishments_df,
            "slot_assignments": slot_assignments_df,
        }

        if self.config.dataset.anomaly_injection.enabled:
            ground_truth_df = self._inject_labelled_anomalies(rng, tables)
        else:
            ground_truth_df = pd.DataFrame(
                columns=[
                    "anomaly_id",
                    "expected_issue_type",
                    "expected_category",
                    "expected_severity",
                    "entity_type",
                    "entity_id",
                    "related_entity_ids",
                    "expected_valid_assignment",
                    "rule_violated",
                    "description",
                ]
            )

        # Deterministic primary-key sorting across all tables
        pk_map = {
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
        for name, pk_cols in pk_map.items():
            tables[name] = tables[name].sort_values(pk_cols, kind="mergesort").reset_index(drop=True)
        if not ground_truth_df.empty:
            ground_truth_df = ground_truth_df.sort_values("anomaly_id", kind="mergesort").reset_index(drop=True)

        manifest = {
            "dataset_version": f"slotwise-{self.profile}-v1-seed{self.seed}",
            "profile": self.profile,
            "seed": self.seed,
            "config_version": self.config.project.config_version,
            "reference_observation_start": self.config.dataset.observation_start_date,
            "observation_days": self.obs_days,
            "reference_snapshot_timestamp": self.snapshot_dt.isoformat(),
            "entity_counts": {k: int(len(v)) for k, v in tables.items()},
            "ground_truth_anomaly_count": int(len(ground_truth_df)),
            "ground_truth_breakdown": (
                ground_truth_df["expected_issue_type"].value_counts().sort_index().to_dict()
                if not ground_truth_df.empty
                else {}
            ),
            "nondeterministic_fields": [],
        }
        return GeneratedDatasetBundle(
            profile=self.profile,
            seed=self.seed,
            tables=tables,
            ground_truth=ground_truth_df,
            manifest=manifest,
        )

    def export_bundle(
        self,
        bundle: GeneratedDatasetBundle,
        output_root: Path = DATA_DIR,
        export_sample: bool = True,
    ) -> dict[str, Any]:
        """Export CSV, JSON, SQL seed script, separated ground truth, and SHA-256 manifest."""
        gen_dir = output_root / "generated" / bundle.profile
        exp_dir = output_root / "exports" / bundle.profile
        gt_dir = output_root / "ground_truth" / bundle.profile
        man_dir = output_root / "manifests"

        for directory in (gen_dir, exp_dir, gt_dir, man_dir):
            directory.mkdir(parents=True, exist_ok=True)

        checksums: dict[str, str] = {}

        # 1. Operational CSV & JSON exports (strictly excluding ground truth)
        json_bundle: dict[str, Any] = {}
        for table_name in OPERATIONAL_TABLE_ORDER:
            df = bundle.tables[table_name]
            csv_path = gen_dir / f"{table_name}.csv"
            df.to_csv(csv_path, index=False, lineterminator="\n")
            rel_csv = csv_path.relative_to(output_root).as_posix()
            checksums[rel_csv] = compute_file_sha256(csv_path)
            json_bundle[table_name] = json.loads(df.to_json(orient="records"))

        json_export_path = exp_dir / "operational_dataset.json"
        json_export_path.write_text(
            json.dumps(json_bundle, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        checksums[json_export_path.relative_to(output_root).as_posix()] = compute_file_sha256(json_export_path)

        # 2. SQL-compatible deterministic seed file (batched multi-row INSERTs)
        sql_path = exp_dir / "seed.sql"
        self._write_sql_seed(bundle.tables, sql_path, batch_size=self.config.dataset.batch_size)
        checksums[sql_path.relative_to(output_root).as_posix()] = compute_file_sha256(sql_path)

        # 3. Separated Ground-Truth CSV and JSON
        gt_csv_path = gt_dir / "ground_truth.csv"
        gt_json_path = gt_dir / "ground_truth.json"
        bundle.ground_truth.to_csv(gt_csv_path, index=False, lineterminator="\n")
        gt_records = json.loads(bundle.ground_truth.to_json(orient="records"))
        for rec in gt_records:
            if isinstance(rec.get("related_entity_ids"), str):
                rec["related_entity_ids"] = json.loads(rec["related_entity_ids"])
        gt_json_path.write_text(
            json.dumps(
                {
                    "dataset_version": bundle.manifest["dataset_version"],
                    "profile": bundle.profile,
                    "seed": bundle.seed,
                    "total_anomalies": len(gt_records),
                    "anomalies": gt_records,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        checksums[gt_csv_path.relative_to(output_root).as_posix()] = compute_file_sha256(gt_csv_path)
        checksums[gt_json_path.relative_to(output_root).as_posix()] = compute_file_sha256(gt_json_path)

        # 4. Compact sample extract (for quick repository inspection) when running small profile
        if export_sample and bundle.profile == "small":
            sample_dir = output_root / "sample"
            sample_dir.mkdir(parents=True, exist_ok=True)
            for sample_tbl in ("warehouses", "zones", "locations", "skus", "sku_profiles", "slot_assignments"):
                sample_path = sample_dir / f"{sample_tbl}_sample.csv"
                bundle.tables[sample_tbl].head(25).to_csv(sample_path, index=False, lineterminator="\n")

        # 5. Reproducibility manifest with SHA-256 checksums
        full_manifest = dict(bundle.manifest)
        full_manifest["file_checksums_sha256"] = dict(sorted(checksums.items()))
        manifest_path = man_dir / f"{bundle.profile}_manifest.json"
        manifest_path.write_text(
            json.dumps(full_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        bundle.manifest.update(full_manifest)
        return full_manifest

    @staticmethod
    def _write_sql_seed(tables: dict[str, pd.DataFrame], sql_path: Path, batch_size: int = 1000) -> None:
        """Stream batched SQL INSERT statements without loading giant string copies into memory."""

        def _sql_literal(val: Any) -> str:
            if val is None or (isinstance(val, float) and np.isnan(val)):
                return "NULL"
            if isinstance(val, (bool, np.bool_)):
                return "1" if bool(val) else "0"
            if isinstance(val, (int, np.integer)):
                return str(int(val))
            if isinstance(val, (float, np.floating)):
                formatted = f"{float(val):.6f}".rstrip("0").rstrip(".")
                return formatted if formatted else "0"
            escaped = str(val).replace("'", "''")
            return f"'{escaped}'"

        with sql_path.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write("-- SlotWise AI Deterministic SQL Seed Export\n")
            fh.write("BEGIN TRANSACTION;\n\n")
            for table_name in OPERATIONAL_TABLE_ORDER:
                df = tables[table_name]
                if df.empty:
                    continue
                cols = ", ".join(df.columns.tolist())
                records = df.to_dict(orient="records")
                for start in range(0, len(records), min(batch_size, 500)):
                    chunk = records[start : start + min(batch_size, 500)]
                    values_rows = [
                        "(" + ", ".join(_sql_literal(row[c]) for c in df.columns) + ")" for row in chunk
                    ]
                    fh.write(f"INSERT INTO {table_name} ({cols}) VALUES\n  ")
                    fh.write(",\n  ".join(values_rows))
                    fh.write(";\n")
                fh.write("\n")
            fh.write("COMMIT;\n")

    def _build_hazard_classes(self) -> pd.DataFrame:
        rows = [
            (
                HazardCode.NONE.value,
                "Non-Hazardous General Goods",
                False,
                0,
                "No special hazmat segregation required.",
            ),
            (
                HazardCode.FLAMMABLE.value,
                "Class 3 Flammable Materials",
                True,
                3,
                "Keep segregated from oxidizers, corrosives, and battery packs.",
            ),
            (
                HazardCode.CORROSIVE.value,
                "Class 8 Corrosive Substances",
                True,
                3,
                "Acids/alkalis; segregate from flammables, oxidizers, and food.",
            ),
            (
                HazardCode.OXIDIZER.value,
                "Class 5.1 Oxidizing Agents",
                True,
                4,
                "High reactivity; strict separation from flammables and corrosives.",
            ),
            (
                HazardCode.TOXIC.value,
                "Class 6.1 Toxic Substances",
                True,
                4,
                "Poisonous substances; prohibit co-location with general/food goods.",
            ),
            (
                HazardCode.LITHIUM_BATTERY.value,
                "Class 9 Lithium-Ion Batteries",
                True,
                2,
                "Thermal runaway control; segregate from Class 3 flammables.",
            ),
        ]
        return pd.DataFrame(
            rows,
            columns=["hazard_class_id", "class_name", "requires_segregation", "severity_level", "description"],
        )

    def _build_compatibility_rules(self) -> pd.DataFrame:
        raw_rules = [
            (
                "HAZARD_CO_LOCATION",
                "FLAMMABLE",
                "OXIDIZER",
                "SAME_LOCATION",
                "CRITICAL",
                True,
                "Flammable materials must never share a slot with oxidizers.",
            ),
            (
                "HAZARD_CO_LOCATION",
                "FLAMMABLE",
                "CORROSIVE",
                "SAME_LOCATION",
                "CRITICAL",
                True,
                "Flammable materials must not co-locate with corrosive agents.",
            ),
            (
                "HAZARD_CO_LOCATION",
                "CORROSIVE",
                "OXIDIZER",
                "SAME_LOCATION",
                "CRITICAL",
                True,
                "Corrosive substances must not co-locate with oxidizers.",
            ),
            (
                "HAZARD_CO_LOCATION",
                "TOXIC",
                "NONE",
                "SAME_LOCATION",
                "CRITICAL",
                True,
                "Toxic materials cannot share locations with non-hazardous stock.",
            ),
            (
                "CATEGORY_CO_LOCATION",
                "FOOD_BEVERAGE",
                "CHEMICAL_CLEANING",
                "SAME_LOCATION",
                "CRITICAL",
                True,
                "Food and beverage SKUs cannot co-locate with chemical cleaning agents.",
            ),
            (
                "CATEGORY_CO_LOCATION",
                "COLD_PRODUCE",
                "CHEMICAL_CLEANING",
                "SAME_LOCATION",
                "CRITICAL",
                True,
                "Fresh/cold produce cannot co-locate with chemical products.",
            ),
            (
                "CATEGORY_CO_LOCATION",
                "PHARMACEUTICAL",
                "CHEMICAL_CLEANING",
                "SAME_LOCATION",
                "HIGH",
                True,
                "Pharmaceuticals must remain separated from industrial/cleaning chemicals.",
            ),
            (
                "HAZARD_CO_LOCATION",
                "LITHIUM_BATTERY",
                "FLAMMABLE",
                "SAME_LOCATION",
                "CRITICAL",
                True,
                "Lithium batteries cannot co-locate with flammable liquids/solids.",
            ),
        ]
        rows = [
            {
                "rule_id": format_rule_id(i + 1),
                "rule_type": r[0],
                "primary_attribute": r[1],
                "conflicting_attribute": r[2],
                "scope": r[3],
                "severity": r[4],
                "is_hard_constraint": r[5],
                "description": r[6],
            }
            for i, r in enumerate(raw_rules)
        ]
        return pd.DataFrame(rows)

    def _build_suppliers(self, rng: np.random.Generator) -> pd.DataFrame:
        n_suppliers = max(12, min(150, self.config.dataset.skus // 15))
        countries = ["India", "Germany", "Japan", "South Korea", "United States", "Singapore", "Vietnam"]
        prefixes = [
            "Apex",
            "Vanguard",
            "Nexus",
            "Prime",
            "Atlas",
            "Meridian",
            "Vertex",
            "zenith",
            "Kinetix",
            "Global",
        ]
        suffixes = [
            "Industrial",
            "Logistics",
            "Biosciences",
            "Consumer Corp",
            "Components",
            "Supply Co",
            "Packaging",
        ]
        rows = []
        for idx in range(1, n_suppliers + 1):
            name = (
                f"{prefixes[(idx - 1) % len(prefixes)].title()} {suffixes[(idx * 3) % len(suffixes)]} #{idx:02d}"
            )
            rows.append(
                {
                    "supplier_id": format_supplier_id(idx),
                    "supplier_name": name,
                    "country": countries[int(rng.integers(0, len(countries)))],
                    "average_lead_time_days": round(float(rng.uniform(2.5, 11.5)), 2),
                    "reliability_score": round(float(rng.uniform(0.87, 0.99)), 3),
                    "status": EntityStatus.ACTIVE.value,
                }
            )
        return pd.DataFrame(rows)

    def _build_warehouse_topology(
        self, rng: np.random.Generator
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        n_wh = self.config.dataset.warehouses
        total_locs = self.config.dataset.locations

        wh_types = [
            WarehouseType.FULFILLMENT_CENTER.value,
            WarehouseType.REGIONAL_DC.value,
            WarehouseType.COLD_STORAGE_HUB.value,
            WarehouseType.HAZMAT_FACILITY.value,
        ]
        op_hours_choices = ["24/7", "06:00-22:00", "05:00-23:00", "07:00-21:00"]

        # Allocate locations across warehouses with realistic size variation
        wh_size_weights = rng.uniform(0.78, 1.28, size=n_wh)
        min_locs_per_wh = len(ZONE_TEMPLATES) * 4
        wh_loc_counts = _allocate_exact_counts(total_locs, wh_size_weights, min_each=min_locs_per_wh)

        warehouses_rows: list[dict[str, Any]] = []
        zones_rows: list[dict[str, Any]] = []
        bridge_rows: list[dict[str, Any]] = []
        aisles_rows: list[dict[str, Any]] = []
        locations_rows: list[dict[str, Any]] = []
        pickers_rows: list[dict[str, Any]] = []

        zone_counter = 0
        created_iso = self.obs_start.isoformat()
        template_shares = np.array([t["share"] for t in ZONE_TEMPLATES], dtype=np.float64)

        for w_idx in range(1, n_wh + 1):
            wh_id = format_warehouse_id(w_idx)
            city, state, country = CITY_CATALOG[(w_idx - 1) % len(CITY_CATALOG)]
            wh_locs = wh_loc_counts[w_idx - 1]
            area_sqft = round(float(wh_locs * rng.uniform(210.0, 275.0)), 1)
            # Generous normal labor budget so clean baseline runs at ~45-70% utilization before anomaly injection
            expected_skus_in_wh = max(25.0, self.config.dataset.skus / float(n_wh))
            daily_labor_cap = round(float(max(1200.0, expected_skus_in_wh * 11.5)), 1)

            warehouses_rows.append(
                {
                    "warehouse_id": wh_id,
                    "warehouse_name": f"{city} {wh_types[(w_idx - 1) % len(wh_types)].replace('_', ' ').title()} {w_idx:02d}",
                    "warehouse_type": wh_types[(w_idx - 1) % len(wh_types)],
                    "city": city,
                    "state": state,
                    "country": country,
                    "total_area_sqft": area_sqft,
                    "operating_hours": op_hours_choices[(w_idx - 1) % len(op_hours_choices)],
                    "daily_labor_minutes_capacity": daily_labor_cap,
                    "depot_x": 0.0,
                    "depot_y": 0.0,
                    "status": EntityStatus.ACTIVE.value,
                    "created_at": created_iso,
                }
            )

            # Pickers per warehouse
            n_pickers = max(4, min(20, wh_locs // 60))
            shifts = ["DAY", "EVENING", "NIGHT"]
            for p_idx in range(1, n_pickers + 1):
                pickers_rows.append(
                    {
                        "picker_id": format_picker_id(wh_id, p_idx),
                        "warehouse_id": wh_id,
                        "picker_name": f"Associate-{wh_id[-3:]}-{p_idx:02d}",
                        "shift": shifts[(p_idx - 1) % len(shifts)],
                        "walk_speed_mps": round(float(rng.uniform(1.15, 1.42)), 3),
                        "pick_rate_per_hour": round(float(rng.uniform(55.0, 82.0)), 2),
                        "status": EntityStatus.ACTIVE.value,
                    }
                )

            zone_loc_counts = _allocate_exact_counts(wh_locs, template_shares, min_each=3)

            for t_idx, tmpl in enumerate(ZONE_TEMPLATES):
                zone_counter += 1
                zone_id = format_zone_id(zone_counter)
                z_locs = zone_loc_counts[t_idx]
                sorted_hazards = sorted(tmpl["hazards"])

                for hz_code in sorted_hazards:
                    bridge_rows.append({"zone_id": zone_id, "hazard_class_id": hz_code})

                n_aisles = max(2, min(8, int(np.ceil(z_locs / 22.0))))
                aisle_loc_counts = _allocate_exact_counts(z_locs, np.ones(n_aisles, dtype=np.float64), min_each=1)

                zone_total_max_wt = 0.0
                zone_total_max_vol = 0.0
                loc_seq_in_zone = 0

                for a_idx in range(1, n_aisles + 1):
                    aisle_id = format_aisle_id(zone_id, a_idx)
                    aisle_x = round(float(tmpl["x_offset"] + (a_idx - 1) * 3.6), 2)
                    a_locs = aisle_loc_counts[a_idx - 1]
                    aisle_y_end = round(float(4.0 + max(1, a_locs) * 1.4), 2)

                    aisles_rows.append(
                        {
                            "aisle_id": aisle_id,
                            "zone_id": zone_id,
                            "warehouse_id": wh_id,
                            "aisle_number": a_idx,
                            "x_start": aisle_x,
                            "y_start": 2.0,
                            "x_end": aisle_x,
                            "y_end": aisle_y_end,
                            "width_m": 3.2,
                            "is_blocked": False,
                            "congestion_factor": round(float(rng.uniform(1.0, 1.22)), 3),
                            "status": EntityStatus.ACTIVE.value,
                        }
                    )

                    w_min, w_max, h_min, h_max, d_min, d_max = tmpl["dims"]
                    wt_min, wt_max = tmpl["weight_range"]

                    for slot_i in range(a_locs):
                        loc_seq_in_zone += 1
                        loc_id = format_location_id(zone_id, loc_seq_in_zone)
                        shelf_level = (slot_i % 4) + 1
                        bay_idx = (slot_i // 4) + 1
                        rack_id = f"R{a_idx:02d}-{(bay_idx + 1) // 2:02d}"
                        bin_id = f"B{bay_idx:02d}-L{shelf_level}"

                        loc_x = round(aisle_x + (0.6 if bay_idx % 2 == 0 else -0.6), 2)
                        loc_y = round(2.5 + bay_idx * 1.6, 2)

                        width_cm = round(float(rng.uniform(w_min, w_max)), 1)
                        height_cm = round(float(rng.uniform(h_min, h_max)), 1)
                        depth_cm = round(float(rng.uniform(d_min, d_max)), 1)
                        max_vol = round((width_cm * height_cm * depth_cm) / 1_000_000.0, 6)
                        max_wt = round(float(rng.uniform(wt_min, wt_max)), 2)

                        if tmpl["zone_type"] == ZoneType.OVERSIZE.value or shelf_level == 1:
                            acc_type = (
                                AccessibilityType.FLOOR_PALLET.value
                                if shelf_level == 1
                                else AccessibilityType.HIGH_RACK_FORKLIFT.value
                            )
                        elif shelf_level == 2:
                            acc_type = AccessibilityType.GOLDEN_ZONE.value
                        elif shelf_level == 3:
                            acc_type = AccessibilityType.STANDARD_REACH.value
                        else:
                            acc_type = AccessibilityType.HIGH_RACK_FORKLIFT.value

                        storage_policy = (
                            StoragePolicy.SHARED_COMPATIBLE.value
                            if (loc_seq_in_zone % 7 == 0)
                            else StoragePolicy.DEDICATED_SINGLE_SKU.value
                        )

                        zone_total_max_wt += max_wt
                        zone_total_max_vol += max_vol

                        locations_rows.append(
                            {
                                "location_id": loc_id,
                                "zone_id": zone_id,
                                "aisle_id": aisle_id,
                                "rack_id": rack_id,
                                "shelf_level": shelf_level,
                                "bin_id": bin_id,
                                "x_coordinate": loc_x,
                                "y_coordinate": loc_y,
                                "width_cm": width_cm,
                                "height_cm": height_cm,
                                "depth_cm": depth_cm,
                                "max_weight_kg": max_wt,
                                "max_volume_m3": max_vol,
                                "current_weight_kg": 0.0,
                                "current_volume_m3": 0.0,
                                "accessibility_type": acc_type,
                                "storage_policy": storage_policy,
                                "is_blocked": False,
                                "status": EntityStatus.ACTIVE.value,
                            }
                        )

                zones_rows.append(
                    {
                        "zone_id": zone_id,
                        "warehouse_id": wh_id,
                        "zone_name": f"{wh_id} {tmpl['suffix']}",
                        "zone_type": tmpl["zone_type"],
                        "temperature_type": tmpl["temperature_type"],
                        "allowed_hazard_classes": ",".join(sorted_hazards),
                        "max_weight_kg": round(zone_total_max_wt * 1.1, 2),
                        "max_volume_m3": round(zone_total_max_vol * 1.1, 4),
                        "status": EntityStatus.ACTIVE.value,
                    }
                )

        return (
            pd.DataFrame(warehouses_rows),
            pd.DataFrame(zones_rows),
            pd.DataFrame(bridge_rows),
            pd.DataFrame(aisles_rows),
            pd.DataFrame(locations_rows),
            pd.DataFrame(pickers_rows),
        )

    def _build_skus(
        self,
        rng: np.random.Generator,
        suppliers_df: pd.DataFrame,
        warehouses_df: pd.DataFrame,
    ) -> tuple[pd.DataFrame, dict[str, str]]:
        n_skus = self.config.dataset.skus
        sup_ids = suppliers_df["supplier_id"].tolist()
        wh_ids = warehouses_df["warehouse_id"].tolist()

        # Archetypes aligned cleanly with zone thermal/hazard capacity shares
        archetypes: list[dict[str, Any]] = [
            {
                "cat": SKUCategory.CONSUMER_GOODS.value,
                "temp": TemperatureType.AMBIENT.value,
                "haz": HazardCode.NONE.value,
                "stype": StorageType.BIN.value,
                "prob": 0.24,
                "wt": (0.3, 6.0),
                "dim": (15.0, 40.0),
                "price": (8.0, 75.0),
                "fragile": 0.15,
            },
            {
                "cat": SKUCategory.APPAREL.value,
                "temp": TemperatureType.AMBIENT.value,
                "haz": HazardCode.NONE.value,
                "stype": StorageType.SHELF.value,
                "prob": 0.16,
                "wt": (0.2, 2.5),
                "dim": (18.0, 42.0),
                "price": (14.0, 120.0),
                "fragile": 0.02,
            },
            {
                "cat": SKUCategory.ELECTRONICS.value,
                "temp": TemperatureType.AMBIENT.value,
                "haz": HazardCode.NONE.value,
                "stype": StorageType.BIN.value,
                "prob": 0.10,
                "wt": (0.25, 5.5),
                "dim": (12.0, 38.0),
                "price": (45.0, 680.0),
                "fragile": 0.70,
            },
            {
                "cat": SKUCategory.ELECTRONICS.value,
                "temp": TemperatureType.AMBIENT.value,
                "haz": HazardCode.LITHIUM_BATTERY.value,
                "stype": StorageType.BIN.value,
                "prob": 0.05,
                "wt": (0.4, 4.5),
                "dim": (12.0, 35.0),
                "price": (65.0, 850.0),
                "fragile": 0.65,
            },
            {
                "cat": SKUCategory.INDUSTRIAL_HARDWARE.value,
                "temp": TemperatureType.AMBIENT.value,
                "haz": HazardCode.NONE.value,
                "stype": StorageType.PALLET.value,
                "prob": 0.09,
                "wt": (14.0, 58.0),
                "dim": (60.0, 130.0),
                "price": (55.0, 490.0),
                "fragile": 0.05,
            },
            {
                "cat": SKUCategory.FOOD_BEVERAGE.value,
                "temp": TemperatureType.AMBIENT.value,
                "haz": HazardCode.NONE.value,
                "stype": StorageType.SHELF.value,
                "prob": 0.08,
                "wt": (0.5, 9.0),
                "dim": (16.0, 44.0),
                "price": (4.5, 42.0),
                "fragile": 0.25,
            },
            {
                "cat": SKUCategory.COLD_PRODUCE.value,
                "temp": TemperatureType.CHILLED.value,
                "haz": HazardCode.NONE.value,
                "stype": StorageType.SHELF.value,
                "prob": 0.09,
                "wt": (0.5, 8.0),
                "dim": (18.0, 45.0),
                "price": (6.0, 55.0),
                "fragile": 0.35,
            },
            {
                "cat": SKUCategory.COLD_PRODUCE.value,
                "temp": TemperatureType.FROZEN.value,
                "haz": HazardCode.NONE.value,
                "stype": StorageType.SHELF.value,
                "prob": 0.06,
                "wt": (0.8, 10.0),
                "dim": (18.0, 46.0),
                "price": (9.0, 68.0),
                "fragile": 0.20,
            },
            {
                "cat": SKUCategory.PHARMACEUTICAL.value,
                "temp": TemperatureType.CONTROLLED_ROOM.value,
                "haz": HazardCode.NONE.value,
                "stype": StorageType.BIN.value,
                "prob": 0.06,
                "wt": (0.1, 2.2),
                "dim": (10.0, 28.0),
                "price": (85.0, 950.0),
                "fragile": 0.55,
            },
            {
                "cat": SKUCategory.CHEMICAL_CLEANING.value,
                "temp": TemperatureType.AMBIENT.value,
                "haz": HazardCode.NONE.value,
                "stype": StorageType.SHELF.value,
                "prob": 0.025,
                "wt": (1.0, 8.0),
                "dim": (16.0, 38.0),
                "price": (12.0, 85.0),
                "fragile": 0.15,
            },
            {
                "cat": SKUCategory.CHEMICAL_CLEANING.value,
                "temp": TemperatureType.AMBIENT.value,
                "haz": HazardCode.FLAMMABLE.value,
                "stype": StorageType.HAZMAT_CABINET.value,
                "prob": 0.025,
                "wt": (1.5, 14.0),
                "dim": (20.0, 48.0),
                "price": (18.0, 140.0),
                "fragile": 0.20,
            },
            {
                "cat": SKUCategory.CHEMICAL_CLEANING.value,
                "temp": TemperatureType.AMBIENT.value,
                "haz": HazardCode.CORROSIVE.value,
                "stype": StorageType.HAZMAT_CABINET.value,
                "prob": 0.02,
                "wt": (2.0, 16.0),
                "dim": (20.0, 48.0),
                "price": (22.0, 150.0),
                "fragile": 0.20,
            },
            {
                "cat": SKUCategory.CHEMICAL_CLEANING.value,
                "temp": TemperatureType.AMBIENT.value,
                "haz": HazardCode.OXIDIZER.value,
                "stype": StorageType.HAZMAT_CABINET.value,
                "prob": 0.015,
                "wt": (1.5, 12.0),
                "dim": (20.0, 45.0),
                "price": (28.0, 175.0),
                "fragile": 0.20,
            },
            {
                "cat": SKUCategory.CHEMICAL_CLEANING.value,
                "temp": TemperatureType.AMBIENT.value,
                "haz": HazardCode.TOXIC.value,
                "stype": StorageType.HAZMAT_CABINET.value,
                "prob": 0.01,
                "wt": (1.0, 10.0),
                "dim": (18.0, 42.0),
                "price": (35.0, 210.0),
                "fragile": 0.25,
            },
        ]
        probs = np.array([a["prob"] for a in archetypes], dtype=np.float64)
        probs = probs / np.sum(probs)

        arch_indices = rng.choice(len(archetypes), size=n_skus, p=probs)
        rows: list[dict[str, Any]] = []
        sku_home_wh: dict[str, str] = {}

        for idx in range(1, n_skus + 1):
            sku_id = format_sku_id(idx)
            arch = archetypes[int(arch_indices[idx - 1])]
            d_low, d_high = arch["dim"]
            wt_low, wt_high = arch["wt"]
            p_low, p_high = arch["price"]

            # Log-uniform price for realistic ABC Pareto spread
            unit_price = round(float(np.exp(rng.uniform(np.log(p_low), np.log(p_high)))), 2)
            unit_weight = round(float(rng.uniform(wt_low, wt_high)), 3)
            width_cm = round(float(rng.uniform(d_low, d_high)), 1)
            height_cm = round(float(rng.uniform(d_low * 0.8, d_high * 0.9)), 1)
            depth_cm = round(float(rng.uniform(d_low * 0.85, d_high * 0.95)), 1)

            home_wh = wh_ids[(idx - 1) % len(wh_ids)]
            sku_home_wh[sku_id] = home_wh

            rows.append(
                {
                    "sku_id": sku_id,
                    "sku_name": f"{arch['cat'].replace('_', ' ').title()} Item {idx:05d}",
                    "category": arch["cat"],
                    "unit_price": unit_price,
                    "unit_weight_kg": unit_weight,
                    "width_cm": width_cm,
                    "height_cm": height_cm,
                    "depth_cm": depth_cm,
                    "is_fragile": bool(rng.random() < arch["fragile"]),
                    "hazard_class": arch["haz"],
                    "temperature_requirement": arch["temp"],
                    "storage_type": arch["stype"],
                    "supplier_id": sup_ids[int(rng.integers(0, len(sup_ids)))],
                    "status": SKUStatus.ACTIVE.value,
                }
            )
        return pd.DataFrame(rows), sku_home_wh

    def _build_orders_and_lines(
        self,
        rng: np.random.Generator,
        warehouses_df: pd.DataFrame,
        skus_df: pd.DataFrame,
        sku_home_wh: dict[str, str],
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        target_lines = self.config.dataset.order_lines
        wh_ids = warehouses_df["warehouse_id"].tolist()
        wh_areas = warehouses_df["total_area_sqft"].to_numpy(dtype=np.float64)
        wh_probs = wh_areas / np.sum(wh_areas)

        sku_prices = skus_df.set_index("sku_id")["unit_price"].to_dict()
        sku_cats = skus_df.set_index("sku_id")["category"].to_dict()

        # Group SKUs by home warehouse and assign Zipf popularity weights within each warehouse
        wh_skus: dict[str, list[str]] = {w: [] for w in wh_ids}
        for sid, wid in sku_home_wh.items():
            wh_skus[wid].append(sid)

        wh_sku_probs: dict[str, np.ndarray] = {}
        for wid, s_list in wh_skus.items():
            perm = rng.permutation(len(s_list))
            ranks = np.empty(len(s_list), dtype=np.float64)
            ranks[perm] = np.arange(1, len(s_list) + 1, dtype=np.float64)
            weights = np.power(ranks + 2.0, -1.18)
            wh_sku_probs[wid] = weights / np.sum(weights)

        # Build 90-day temporal demand probability distribution (weekday + seasonal + 2 promo peaks)
        day_weights = np.zeros(self.obs_days, dtype=np.float64)
        dow_mult = [1.18, 1.15, 1.10, 1.08, 1.05, 0.76, 0.68]
        for d in range(self.obs_days):
            dt = self.obs_start + timedelta(days=d)
            seasonal = 1.0 + 0.18 * np.sin(2.0 * np.pi * d / float(self.obs_days))
            promo = 1.75 if (24 <= d <= 28 or 68 <= d <= 73) else 1.0
            day_weights[d] = dow_mult[dt.weekday()] * seasonal * promo
        day_probs = day_weights / np.sum(day_weights)

        # Partition target_lines into realistic multi-line orders (1..7 lines per order)
        line_counts: list[int] = []
        acc = 0
        while acc < target_lines:
            k = int(rng.choice([1, 2, 3, 4, 5, 6, 7], p=[0.18, 0.26, 0.24, 0.15, 0.09, 0.05, 0.03]))
            if acc + k > target_lines:
                k = target_lines - acc
            line_counts.append(k)
            acc += k

        n_orders = len(line_counts)
        order_days = np.sort(rng.choice(self.obs_days, size=n_orders, p=day_probs))
        order_wh_indices = rng.choice(len(wh_ids), size=n_orders, p=wh_probs)
        priorities = [
            OrderPriority.STANDARD.value,
            OrderPriority.EXPRESS.value,
            OrderPriority.SAME_DAY.value,
            OrderPriority.BULK.value,
        ]
        prio_probs = [0.62, 0.23, 0.09, 0.06]
        order_prios = rng.choice(priorities, size=n_orders, p=prio_probs)
        regions = ["NORTH", "SOUTH", "EAST", "WEST", "CENTRAL"]
        order_regions = rng.choice(regions, size=n_orders)

        orders_rows: list[dict[str, Any]] = []
        lines_rows: list[dict[str, Any]] = []

        for o_idx in range(n_orders):
            order_id = format_order_id(self.year, o_idx + 1)
            wid = wh_ids[int(order_wh_indices[o_idx])]
            day_offset = int(order_days[o_idx])
            sec_offset = int(rng.integers(21600, 79200))  # 06:00 to 22:00
            order_dt = self.obs_start + timedelta(days=day_offset, seconds=sec_offset)
            prio = str(order_prios[o_idx])

            # Last 2 days have some PICKING/PENDING orders; historical days are COMPLETED
            if day_offset >= self.obs_days - 2 and rng.random() < 0.25:
                status = OrderStatus.PICKING.value if rng.random() < 0.6 else OrderStatus.PENDING.value
            else:
                status = OrderStatus.COMPLETED.value

            orders_rows.append(
                {
                    "order_id": order_id,
                    "warehouse_id": wid,
                    "order_date": order_dt.isoformat(),
                    "order_priority": prio,
                    "order_status": status,
                    "customer_region": str(order_regions[o_idx]),
                    "created_at": order_dt.isoformat(),
                }
            )

            k_lines = line_counts[o_idx]
            avail_skus = wh_skus[wid]
            replace_flag = k_lines > len(avail_skus)
            chosen_indices = rng.choice(len(avail_skus), size=k_lines, replace=replace_flag, p=wh_sku_probs[wid])

            for l_seq, s_idx in enumerate(chosen_indices, start=1):
                sid = avail_skus[int(s_idx)]
                cat = sku_cats[sid]
                base_qty = int(rng.geometric(p=0.36))
                if prio == OrderPriority.BULK.value:
                    base_qty *= int(rng.integers(3, 7))
                if cat in {SKUCategory.INDUSTRIAL_HARDWARE.value, SKUCategory.PHARMACEUTICAL.value}:
                    qty = max(1, min(18, base_qty))
                else:
                    qty = max(1, min(40, base_qty))

                u_price = float(sku_prices[sid])
                line_val = round(qty * u_price, 2)
                lines_rows.append(
                    {
                        "order_line_id": format_order_line_id(order_id, l_seq),
                        "order_id": order_id,
                        "sku_id": sid,
                        "quantity": qty,
                        "unit_price": u_price,
                        "line_value": line_val,
                    }
                )

        return pd.DataFrame(orders_rows), pd.DataFrame(lines_rows)

    def _build_baseline_assignments(
        self,
        rng: np.random.Generator,
        skus_df: pd.DataFrame,
        sku_profiles_df: pd.DataFrame,
        sku_home_wh: dict[str, str],
        zones_df: pd.DataFrame,
        bridge_df: pd.DataFrame,
        locations_df: pd.DataFrame,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Assign each SKU to a physically & environmentally valid baseline slot.

        Deliberately selects mid/back valid slots rather than dock-optimal golden-zone slots
        so baseline data reflects an unoptimized (yet 100% rule-compliant) warehouse layout.
        """
        locs = locations_df.copy()
        zone_meta = zones_df.set_index("zone_id")[["warehouse_id", "zone_type", "temperature_type"]].to_dict(
            orient="index"
        )
        zone_hazards: dict[str, set[str]] = {}
        for row in bridge_df.to_dict(orient="records"):
            zone_hazards.setdefault(row["zone_id"], set()).add(row["hazard_class_id"])

        prof_map = sku_profiles_df.set_index("sku_id").to_dict(orient="index")

        # Index location row positions by warehouse_id
        wh_loc_indices: dict[str, list[int]] = {}
        for idx, z_id in enumerate(locs["zone_id"].tolist()):
            wid = zone_meta[z_id]["warehouse_id"]
            wh_loc_indices.setdefault(wid, []).append(idx)

        # Shuffle candidate search order deterministically preferring mid/rear coordinates
        for wid, idx_list in wh_loc_indices.items():
            # Sort descending by distance from dock with minor deterministic jitter so fast movers start unoptimized
            jitter = rng.uniform(-4.0, 4.0, size=len(idx_list))
            scored = [
                (
                    -(locs.at[i, "x_coordinate"] + 0.5 * locs.at[i, "y_coordinate"] + float(jitter[k])),
                    i,
                )
                for k, i in enumerate(idx_list)
            ]
            scored.sort(key=lambda t: t[0])
            wh_loc_indices[wid] = [i for _, i in scored]

        occupied_loc_indices: set[int] = set()
        assignments_rows: list[dict[str, Any]] = []
        slot_seq = 0

        # Allocate largest/most-constrained SKUs first so small items do not exhaust OVERSIZE bays
        ordered_skus = sorted(
            skus_df.to_dict(orient="records"),
            key=lambda s: (-(float(s["width_cm"]) * float(s["height_cm"]) * float(s["depth_cm"])), s["sku_id"]),
        )

        for sku in ordered_skus:
            sid = sku["sku_id"]
            wid = sku_home_wh[sid]
            prof = prof_map[sid]
            u_wt = float(sku["unit_weight_kg"])
            u_vol = (float(sku["width_cm"]) * float(sku["height_cm"]) * float(sku["depth_cm"])) / 1_000_000.0

            selected_loc_idx: int | None = None
            for cand_idx in wh_loc_indices[wid]:
                if cand_idx in occupied_loc_indices:
                    continue
                z_id = locs.at[cand_idx, "zone_id"]
                z_info = zone_meta[z_id]
                if z_info["temperature_type"] != sku["temperature_requirement"]:
                    continue
                if sku["hazard_class"] not in zone_hazards.get(z_id, set()):
                    continue
                # Prefer dedicated HAZMAT zone for regulated hazardous items (except Li-ion allowed in ambient)
                if (
                    sku["hazard_class"] not in {HazardCode.NONE.value, HazardCode.LITHIUM_BATTERY.value}
                    and z_info["zone_type"] != ZoneType.HAZMAT.value
                ):
                    continue
                if (
                    sku["width_cm"] > locs.at[cand_idx, "width_cm"]
                    or sku["height_cm"] > locs.at[cand_idx, "height_cm"]
                    or sku["depth_cm"] > locs.at[cand_idx, "depth_cm"]
                ):
                    continue
                if u_wt > float(locs.at[cand_idx, "max_weight_kg"]) * 0.78:
                    continue
                if u_vol > float(locs.at[cand_idx, "max_volume_m3"]) * 0.78:
                    continue
                if (
                    u_wt > 25.0
                    and locs.at[cand_idx, "accessibility_type"] == AccessibilityType.HIGH_RACK_FORKLIFT.value
                    and sku["storage_type"] != StorageType.PALLET.value
                ):
                    continue
                selected_loc_idx = cand_idx
                break

            if selected_loc_idx is None:
                continue

            occupied_loc_indices.add(selected_loc_idx)
            max_wt = float(locs.at[selected_loc_idx, "max_weight_kg"])
            max_vol = float(locs.at[selected_loc_idx, "max_volume_m3"])

            # Cap baseline assigned quantity at <= 76% of weight and volume capacity
            max_units_wt = max(1, int(np.floor((max_wt * 0.76) / u_wt)))
            max_units_vol = max(1, int(np.floor((max_vol * 0.76) / max(u_vol, 1e-9))))
            desired_qty = max(1, int(prof["current_on_hand_qty"]))
            assigned_qty = max(1, min(desired_qty, max_units_wt, max_units_vol))

            alloc_wt = round(assigned_qty * u_wt, 4)
            alloc_vol = round(assigned_qty * u_vol, 6)
            locs.at[selected_loc_idx, "current_weight_kg"] = alloc_wt
            locs.at[selected_loc_idx, "current_volume_m3"] = alloc_vol

            dist_m = float(locs.at[selected_loc_idx, "x_coordinate"]) + float(
                locs.at[selected_loc_idx, "y_coordinate"]
            )
            dist_score = round(max(0.05, 1.0 - min(dist_m / 120.0, 0.95)), 4)
            vel_pct = float(prof["velocity_percentile"])
            acc = locs.at[selected_loc_idx, "accessibility_type"]
            acc_val = 1.0 if acc == AccessibilityType.GOLDEN_ZONE.value else 0.55
            vel_score = round(1.0 - abs(vel_pct - acc_val), 4)
            cap_util = max(alloc_wt / max_wt, alloc_vol / max_vol)
            cap_score = round(max(0.1, 1.0 - abs(cap_util - 0.78) / 0.78), 4)
            comp_score = 0.95
            rep_score = 0.70
            freq_score = round(1.0 - abs(vel_pct - dist_score), 4)
            total_score = round(
                0.30 * vel_score
                + 0.20 * freq_score
                + 0.15 * dist_score
                + 0.15 * cap_score
                + 0.10 * rep_score
                + 0.10 * comp_score,
                4,
            )

            slot_seq += 1
            assignments_rows.append(
                {
                    "slot_assignment_id": format_slot_assignment_id(self.year, slot_seq),
                    "optimization_run_id": None,
                    "sku_id": sid,
                    "location_id": str(locs.at[selected_loc_idx, "location_id"]),
                    "warehouse_id": wid,
                    "assignment_status": AssignmentStatus.ACTIVE.value,
                    "assigned_quantity": assigned_qty,
                    "allocated_weight_kg": alloc_wt,
                    "allocated_volume_m3": alloc_vol,
                    "assignment_score": total_score,
                    "distance_score": dist_score,
                    "compatibility_score": comp_score,
                    "capacity_score": cap_score,
                    "velocity_score": vel_score,
                    "pick_frequency_score": freq_score,
                    "replenishment_score": rep_score,
                    "effective_from": self.obs_start.isoformat(),
                    "effective_to": None,
                    "created_at": self.obs_start.isoformat(),
                    "decision_version": 1,
                    "decision_reason": "Legacy baseline first-fit warehouse slotting allocation.",
                }
            )

        return pd.DataFrame(assignments_rows), locs

    def _build_pick_events(
        self,
        rng: np.random.Generator,
        orders_df: pd.DataFrame,
        order_lines_df: pd.DataFrame,
        slot_assignments_df: pd.DataFrame,
        locations_df: pd.DataFrame,
        warehouses_df: pd.DataFrame,
        pickers_df: pd.DataFrame,
    ) -> pd.DataFrame:
        sku_to_loc = slot_assignments_df.set_index("sku_id")["location_id"].to_dict()
        loc_coords = locations_df.set_index("location_id")[["x_coordinate", "y_coordinate", "aisle_id"]].to_dict(
            orient="index"
        )
        wh_depots = warehouses_df.set_index("warehouse_id")[["depot_x", "depot_y"]].to_dict(orient="index")

        wh_pickers: dict[str, list[dict[str, Any]]] = {}
        for p in pickers_df.to_dict(orient="records"):
            wh_pickers.setdefault(p["warehouse_id"], []).append(p)

        order_meta = orders_df.set_index("order_id")[["warehouse_id", "order_date", "order_status"]].to_dict(
            orient="index"
        )

        lines_by_order: dict[str, list[dict[str, Any]]] = {}
        for line in order_lines_df.to_dict(orient="records"):
            lines_by_order.setdefault(line["order_id"], []).append(line)

        pick_rows: list[dict[str, Any]] = []
        handle_line_sec = self.config.simulation.picking.pick_handling_seconds_per_line
        handle_unit_sec = self.config.simulation.picking.pick_handling_seconds_per_unit

        for oid, o_info in order_meta.items():
            if o_info["order_status"] not in {OrderStatus.COMPLETED.value, OrderStatus.PICKING.value}:
                continue
            wid = o_info["warehouse_id"]
            p_list = wh_pickers[wid]
            picker = p_list[int(rng.integers(0, len(p_list)))]
            walk_speed = float(picker["walk_speed_mps"])
            depot = wh_depots[wid]
            curr_x, curr_y = float(depot["depot_x"]), float(depot["depot_y"])
            curr_ts = datetime.fromisoformat(o_info["order_date"]) + timedelta(minutes=15)

            # Baseline unoptimized pick order follows order-line entry sequence
            o_lines = lines_by_order.get(oid, [])
            for seq, line in enumerate(o_lines, start=1):
                sid = line["sku_id"]
                loc_id = sku_to_loc.get(sid)
                if loc_id is None:
                    continue
                l_info = loc_coords[loc_id]
                nx, ny = float(l_info["x_coordinate"]), float(l_info["y_coordinate"])
                travel_m = round(abs(nx - curr_x) + abs(ny - curr_y), 3)
                qty = int(line["quantity"])
                duration_s = round((travel_m / walk_speed) + handle_line_sec + qty * handle_unit_sec, 2)
                curr_ts = curr_ts + timedelta(seconds=max(5, int(round(duration_s))))
                curr_x, curr_y = nx, ny

                pick_rows.append(
                    {
                        "pick_event_id": format_pick_event_id(oid, seq),
                        "order_id": oid,
                        "order_line_id": line["order_line_id"],
                        "sku_id": sid,
                        "location_id": loc_id,
                        "quantity_picked": qty,
                        "picker_id": picker["picker_id"],
                        "pick_timestamp": curr_ts.isoformat(),
                        "pick_duration_seconds": duration_s,
                        "travel_distance_m": travel_m,
                        "aisle_sequence": seq,
                        "event_status": PickEventStatus.COMPLETED.value,
                    }
                )

        return pd.DataFrame(pick_rows)

    def _build_baseline_replenishments(
        self,
        warehouses_df: pd.DataFrame,
        zones_df: pd.DataFrame,
        locations_df: pd.DataFrame,
        skus_df: pd.DataFrame,
        sku_profiles_df: pd.DataFrame,
        slot_assignments_df: pd.DataFrame,
    ) -> pd.DataFrame:
        wh_labor_cap = warehouses_df.set_index("warehouse_id")["daily_labor_minutes_capacity"].to_dict()
        wh_labor_used: dict[str, float] = {w: 0.0 for w in wh_labor_cap}

        # Map each warehouse to its bulk/oversize reserve locations as replenishment sources
        bulk_zones = set(zones_df[zones_df["zone_type"] == ZoneType.OVERSIZE.value]["zone_id"].tolist())
        zone_to_wh = zones_df.set_index("zone_id")["warehouse_id"].to_dict()
        wh_reserve_locs: dict[str, list[str]] = {}
        loc_xy: dict[str, tuple[float, float]] = {}
        for loc in locations_df.to_dict(orient="records"):
            loc_xy[loc["location_id"]] = (float(loc["x_coordinate"]), float(loc["y_coordinate"]))
            if loc["zone_id"] in bulk_zones:
                wid = zone_to_wh[loc["zone_id"]]
                wh_reserve_locs.setdefault(wid, []).append(loc["location_id"])

        assn_map = slot_assignments_df.set_index("sku_id").to_dict(orient="index")
        rep_cfg = self.config.simulation.replenishment
        rep_rows: list[dict[str, Any]] = []
        rep_seq = 0

        for prof in sku_profiles_df.to_dict(orient="records"):
            sid = prof["sku_id"]
            if sid not in assn_map:
                continue
            assn = assn_map[sid]
            on_hand = int(assn["assigned_quantity"])
            rop = float(prof["reorder_point"])
            if on_hand >= rop or rop <= 0.0:
                continue

            wid = assn["warehouse_id"]
            dest_loc = assn["location_id"]
            res_list = wh_reserve_locs.get(wid, [])
            src_loc = res_list[rep_seq % len(res_list)] if res_list else None
            if src_loc == dest_loc:
                src_loc = None

            target_stock = max(int(np.ceil(rop * 1.35)), on_hand + 5)
            req_qty = max(1, target_stock - on_hand)

            if src_loc and src_loc in loc_xy and dest_loc in loc_xy:
                sx, sy = loc_xy[src_loc]
                dx, dy = loc_xy[dest_loc]
                travel_dist = abs(dx - sx) + abs(dy - sy)
            else:
                travel_dist = 35.0

            labor_req = round(
                rep_cfg.base_setup_minutes
                + (travel_dist / rep_cfg.travel_speed_mps) / 60.0
                + (req_qty / 100.0) * rep_cfg.handling_minutes_per_100_units,
                3,
            )

            # Keep baseline warehouses safely under 78% of daily labor capacity before overload injection
            if wh_labor_used[wid] + labor_req > wh_labor_cap[wid] * 0.78:
                continue

            wh_labor_used[wid] += labor_req
            rep_seq += 1
            prio = (
                ReplenishmentPriority.CRITICAL.value
                if prof["demand_velocity"] == "FAST"
                else ReplenishmentPriority.NORMAL.value
            )
            rep_rows.append(
                {
                    "replenishment_id": format_replenishment_id(self.year, rep_seq),
                    "warehouse_id": wid,
                    "sku_id": sid,
                    "source_location_id": src_loc,
                    "destination_location_id": dest_loc,
                    "required_quantity": req_qty,
                    "replenishment_quantity": req_qty,
                    "priority": prio,
                    "labor_minutes_required": labor_req,
                    "assigned_labor_minutes": labor_req,
                    "replenishment_status": ReplenishmentStatus.PENDING.value,
                    "created_at": self.snapshot_dt.isoformat(),
                    "completed_at": None,
                }
            )

        _ = skus_df  # validated master alignment
        return pd.DataFrame(rep_rows)

    def _inject_labelled_anomalies(
        self,
        rng: np.random.Generator,
        tables: dict[str, pd.DataFrame],
    ) -> pd.DataFrame:
        """Inject isolated, explicitly documented anomalies across disjoint entities."""
        inj = self.config.dataset.anomaly_injection
        locs_df = tables["locations"]
        skus_df = tables["skus"]
        assn_df = tables["slot_assignments"]
        aisles_df = tables["aisles"]
        picks_df = tables["pick_events"]
        reps_df = tables["replenishments"]
        wh_df = tables["warehouses"]
        zones_df = tables["zones"]

        used_loc_ids: set[str] = set()
        used_sku_ids: set[str] = set()
        used_aisle_ids: set[str] = set()
        gt_rows: list[dict[str, Any]] = []
        gt_seq = 0
        next_slot_seq = len(assn_df) + 1
        next_rep_seq = len(reps_df) + 1

        def _add_gt(
            issue_type: ExceptionIssueType,
            category: ExceptionCategory,
            severity: ExceptionSeverity,
            entity_type: str,
            entity_id: str,
            related_ids: list[str],
            valid_assignment: bool,
            rule_violated: str,
            description: str,
        ) -> None:
            nonlocal gt_seq
            gt_seq += 1
            gt_rows.append(
                {
                    "anomaly_id": f"GT-{self.year:04d}-{gt_seq:04d}",
                    "expected_issue_type": issue_type.value,
                    "expected_category": category.value,
                    "expected_severity": severity.value,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "related_entity_ids": json.dumps(related_ids),
                    "expected_valid_assignment": valid_assignment,
                    "rule_violated": rule_violated,
                    "description": description,
                }
            )

        sku_lookup = skus_df.set_index("sku_id").to_dict(orient="index")
        loc_idx_by_id = {lid: i for i, lid in enumerate(locs_df["location_id"].tolist())}
        zone_to_type = zones_df.set_index("zone_id")["zone_type"].to_dict()

        # 1. CAPACITY_EXCEEDED
        active_assns = assn_df[assn_df["assignment_status"] == AssignmentStatus.ACTIVE.value].index.tolist()
        cap_created = 0
        for a_idx in active_assns:
            if cap_created >= inj.capacity_exceeded_count:
                break
            lid = str(assn_df.at[a_idx, "location_id"])
            sid = str(assn_df.at[a_idx, "sku_id"])
            if lid in used_loc_ids or sid in used_sku_ids:
                continue
            l_idx = loc_idx_by_id[lid]
            max_wt = float(locs_df.at[l_idx, "max_weight_kg"])
            u_wt = float(sku_lookup[sid]["unit_weight_kg"])
            u_vol = (
                float(sku_lookup[sid]["width_cm"])
                * float(sku_lookup[sid]["height_cm"])
                * float(sku_lookup[sid]["depth_cm"])
                / 1_000_000.0
            )
            overload_qty = int(np.ceil((max_wt * 1.28) / u_wt)) + 1
            over_wt = round(overload_qty * u_wt, 4)
            over_vol = round(overload_qty * u_vol, 6)

            assn_df.at[a_idx, "assigned_quantity"] = overload_qty
            assn_df.at[a_idx, "allocated_weight_kg"] = over_wt
            assn_df.at[a_idx, "allocated_volume_m3"] = over_vol
            locs_df.at[l_idx, "current_weight_kg"] = over_wt
            locs_df.at[l_idx, "current_volume_m3"] = over_vol

            used_loc_ids.add(lid)
            used_sku_ids.add(sid)
            cap_created += 1
            _add_gt(
                issue_type=ExceptionIssueType.CAPACITY_EXCEEDED,
                category=ExceptionCategory.BUSINESS_RULE_VIOLATION,
                severity=ExceptionSeverity.CRITICAL,
                entity_type="LOCATION",
                entity_id=lid,
                related_ids=[str(assn_df.at[a_idx, "slot_assignment_id"]), sid],
                valid_assignment=False,
                rule_violated="Location occupied weight/volume must not exceed max_weight_kg or max_volume_m3.",
                description=f"Location {lid} overloaded to {over_wt:.2f} kg against max {max_wt:.2f} kg by {sid}.",
            )

        # 2. INCOMPATIBLE_COLOCATION (Flammable + Oxidizer OR Food + Chemical Cleaning in shared slot)
        new_assn_rows: list[dict[str, Any]] = []
        incomp_created = 0
        # Find hazmat/food assignments and pair with conflicting SKUs in the same warehouse
        wh_skus_by_haz: dict[tuple[str, str], list[str]] = {}
        wh_skus_by_cat: dict[tuple[str, str], list[str]] = {}
        for a_row in assn_df.to_dict(orient="records"):
            s_id = a_row["sku_id"]
            w_id = a_row["warehouse_id"]
            wh_skus_by_haz.setdefault((w_id, sku_lookup[s_id]["hazard_class"]), []).append(s_id)
            wh_skus_by_cat.setdefault((w_id, sku_lookup[s_id]["category"]), []).append(s_id)

        for a_idx in active_assns:
            if incomp_created >= inj.incompatible_colocation_count:
                break
            lid = str(assn_df.at[a_idx, "location_id"])
            sid = str(assn_df.at[a_idx, "sku_id"])
            wid = str(assn_df.at[a_idx, "warehouse_id"])
            if lid in used_loc_ids or sid in used_sku_ids:
                continue
            s_info = sku_lookup[sid]
            conflict_candidates: list[str] = []
            if s_info["hazard_class"] == HazardCode.FLAMMABLE.value:
                conflict_candidates = wh_skus_by_haz.get(
                    (wid, HazardCode.OXIDIZER.value), []
                ) + wh_skus_by_haz.get((wid, HazardCode.CORROSIVE.value), [])
            elif s_info["category"] == SKUCategory.FOOD_BEVERAGE.value:
                conflict_candidates = [
                    cid
                    for cid in wh_skus_by_cat.get((wid, SKUCategory.CHEMICAL_CLEANING.value), [])
                    if sku_lookup[cid]["hazard_class"] == HazardCode.NONE.value
                ]

            l_idx = loc_idx_by_id[lid]
            conflict_sid = next(
                (
                    c
                    for c in conflict_candidates
                    if c not in used_sku_ids
                    and c != sid
                    and sku_lookup[c]["width_cm"] <= locs_df.at[l_idx, "width_cm"]
                    and sku_lookup[c]["height_cm"] <= locs_df.at[l_idx, "height_cm"]
                    and sku_lookup[c]["depth_cm"] <= locs_df.at[l_idx, "depth_cm"]
                ),
                None,
            )
            if conflict_sid is None:
                continue

            c_info = sku_lookup[conflict_sid]
            c_wt = float(c_info["unit_weight_kg"])
            c_vol = (
                float(c_info["width_cm"]) * float(c_info["height_cm"]) * float(c_info["depth_cm"])
            ) / 1_000_000.0

            # Ensure shared policy and stay within 85% capacity so ONLY incompatibility triggers
            if (
                float(locs_df.at[l_idx, "current_weight_kg"]) + c_wt
                > float(locs_df.at[l_idx, "max_weight_kg"]) * 0.85
            ):
                continue
            if (
                float(locs_df.at[l_idx, "current_volume_m3"]) + c_vol
                > float(locs_df.at[l_idx, "max_volume_m3"]) * 0.85
            ):
                continue

            locs_df.at[l_idx, "storage_policy"] = StoragePolicy.SHARED_COMPATIBLE.value
            locs_df.at[l_idx, "current_weight_kg"] = round(float(locs_df.at[l_idx, "current_weight_kg"]) + c_wt, 4)
            locs_df.at[l_idx, "current_volume_m3"] = round(
                float(locs_df.at[l_idx, "current_volume_m3"]) + c_vol, 6
            )

            slot_id = format_slot_assignment_id(self.year, next_slot_seq)
            next_slot_seq += 1
            new_assn_rows.append(
                {
                    "slot_assignment_id": slot_id,
                    "optimization_run_id": None,
                    "sku_id": conflict_sid,
                    "location_id": lid,
                    "warehouse_id": wid,
                    "assignment_status": AssignmentStatus.ACTIVE.value,
                    "assigned_quantity": 1,
                    "allocated_weight_kg": round(c_wt, 4),
                    "allocated_volume_m3": round(c_vol, 6),
                    "assignment_score": 0.25,
                    "distance_score": 0.50,
                    "compatibility_score": 0.0,
                    "capacity_score": 0.65,
                    "velocity_score": 0.40,
                    "pick_frequency_score": 0.40,
                    "replenishment_score": 0.50,
                    "effective_from": self.snapshot_dt.isoformat(),
                    "effective_to": None,
                    "created_at": self.snapshot_dt.isoformat(),
                    "decision_version": 2,
                    "decision_reason": "Injected incompatible co-location anomaly record.",
                }
            )
            used_loc_ids.add(lid)
            used_sku_ids.update([sid, conflict_sid])
            incomp_created += 1
            _add_gt(
                issue_type=ExceptionIssueType.INCOMPATIBLE_COLOCATION,
                category=ExceptionCategory.BUSINESS_RULE_VIOLATION,
                severity=ExceptionSeverity.CRITICAL,
                entity_type="LOCATION",
                entity_id=lid,
                related_ids=[str(assn_df.at[a_idx, "slot_assignment_id"]), slot_id, sid, conflict_sid],
                valid_assignment=False,
                rule_violated="Incompatible hazard classes or food/chemical categories must not share a location.",
                description=f"Incompatible SKUs {sid} and {conflict_sid} co-located in {lid}.",
            )

        # 3. DUPLICATE_ACTIVE_ASSIGNMENT on DEDICATED_SINGLE_SKU locations
        dup_created = 0
        compat_ambient_skus_by_wh: dict[str, list[str]] = {}
        for a_row in assn_df.to_dict(orient="records"):
            s_id = a_row["sku_id"]
            s_inf = sku_lookup[s_id]
            if (
                s_inf["temperature_requirement"] == TemperatureType.AMBIENT.value
                and s_inf["hazard_class"] == HazardCode.NONE.value
                and s_inf["category"] == SKUCategory.CONSUMER_GOODS.value
            ):
                compat_ambient_skus_by_wh.setdefault(a_row["warehouse_id"], []).append(s_id)

        for a_idx in active_assns:
            if dup_created >= inj.duplicate_active_assignment_count:
                break
            lid = str(assn_df.at[a_idx, "location_id"])
            sid = str(assn_df.at[a_idx, "sku_id"])
            wid = str(assn_df.at[a_idx, "warehouse_id"])
            if lid in used_loc_ids or sid in used_sku_ids:
                continue
            l_idx = loc_idx_by_id[lid]
            if locs_df.at[l_idx, "storage_policy"] != StoragePolicy.DEDICATED_SINGLE_SKU.value:
                continue
            s_info = sku_lookup[sid]
            if (
                s_info["temperature_requirement"] != TemperatureType.AMBIENT.value
                or s_info["hazard_class"] != HazardCode.NONE.value
                or s_info["category"] != SKUCategory.CONSUMER_GOODS.value
            ):
                continue

            dup_sid = next(
                (
                    c
                    for c in compat_ambient_skus_by_wh.get(wid, [])
                    if c not in used_sku_ids
                    and c != sid
                    and sku_lookup[c]["width_cm"] <= locs_df.at[l_idx, "width_cm"]
                    and sku_lookup[c]["height_cm"] <= locs_df.at[l_idx, "height_cm"]
                    and sku_lookup[c]["depth_cm"] <= locs_df.at[l_idx, "depth_cm"]
                ),
                None,
            )
            if dup_sid is None:
                continue

            d_info = sku_lookup[dup_sid]
            d_wt = float(d_info["unit_weight_kg"])
            d_vol = (
                float(d_info["width_cm"]) * float(d_info["height_cm"]) * float(d_info["depth_cm"])
            ) / 1_000_000.0
            if (
                float(locs_df.at[l_idx, "current_weight_kg"]) + d_wt
                > float(locs_df.at[l_idx, "max_weight_kg"]) * 0.85
            ):
                continue
            if (
                float(locs_df.at[l_idx, "current_volume_m3"]) + d_vol
                > float(locs_df.at[l_idx, "max_volume_m3"]) * 0.85
            ):
                continue

            locs_df.at[l_idx, "current_weight_kg"] = round(float(locs_df.at[l_idx, "current_weight_kg"]) + d_wt, 4)
            locs_df.at[l_idx, "current_volume_m3"] = round(
                float(locs_df.at[l_idx, "current_volume_m3"]) + d_vol, 6
            )

            slot_id = format_slot_assignment_id(self.year, next_slot_seq)
            next_slot_seq += 1
            new_assn_rows.append(
                {
                    "slot_assignment_id": slot_id,
                    "optimization_run_id": None,
                    "sku_id": dup_sid,
                    "location_id": lid,
                    "warehouse_id": wid,
                    "assignment_status": AssignmentStatus.ACTIVE.value,
                    "assigned_quantity": 1,
                    "allocated_weight_kg": round(d_wt, 4),
                    "allocated_volume_m3": round(d_vol, 6),
                    "assignment_score": 0.45,
                    "distance_score": 0.55,
                    "compatibility_score": 0.90,
                    "capacity_score": 0.70,
                    "velocity_score": 0.50,
                    "pick_frequency_score": 0.50,
                    "replenishment_score": 0.60,
                    "effective_from": self.snapshot_dt.isoformat(),
                    "effective_to": None,
                    "created_at": self.snapshot_dt.isoformat(),
                    "decision_version": 2,
                    "decision_reason": "Injected duplicate active assignment on dedicated location.",
                }
            )
            used_loc_ids.add(lid)
            used_sku_ids.update([sid, dup_sid])
            dup_created += 1
            _add_gt(
                issue_type=ExceptionIssueType.DUPLICATE_ACTIVE_ASSIGNMENT,
                category=ExceptionCategory.BUSINESS_RULE_VIOLATION,
                severity=ExceptionSeverity.HIGH,
                entity_type="LOCATION",
                entity_id=lid,
                related_ids=[str(assn_df.at[a_idx, "slot_assignment_id"]), slot_id, sid, dup_sid],
                valid_assignment=False,
                rule_violated="DEDICATED_SINGLE_SKU location cannot hold more than one ACTIVE slot assignment.",
                description=f"Dedicated location {lid} has two active SKU assignments ({sid}, {dup_sid}).",
            )

        if new_assn_rows:
            assn_df = pd.concat([assn_df, pd.DataFrame(new_assn_rows)], ignore_index=True)
            tables["slot_assignments"] = assn_df

        # 4. BLOCKED_AISLE_PICK_PATH: isolate unoccupied aisle, block it, and route exact K4 pick events into a location inside it
        if inj.blocked_aisle_pick_count > 0 and not picks_df.empty:
            occupied_loc_set = set(assn_df["location_id"].tolist())
            free_locs = locs_df[~locs_df["location_id"].isin(occupied_loc_set)]
            aisle_pick_counts = (
                picks_df["location_id"].map(locs_df.set_index("location_id")["aisle_id"].to_dict()).value_counts()
            )

            # Find an aisle with 0 existing pick events that has at least 1 free ambient location
            chosen_aisle_id: str | None = None
            chosen_blocked_loc_id: str | None = None
            for _, f_loc in free_locs.iterrows():
                aid = str(f_loc["aisle_id"])
                zid = str(f_loc["zone_id"])
                if aisle_pick_counts.get(aid, 0) == 0 and zone_to_type.get(zid) == ZoneType.GENERAL.value:
                    chosen_aisle_id = aid
                    chosen_blocked_loc_id = str(f_loc["location_id"])
                    break

            if chosen_aisle_id and chosen_blocked_loc_id:
                aisle_mask = aisles_df["aisle_id"] == chosen_aisle_id
                aisles_df.loc[aisle_mask, "is_blocked"] = True
                aisles_df.loc[aisle_mask, "status"] = EntityStatus.BLOCKED.value
                used_aisle_ids.add(chosen_aisle_id)
                used_loc_ids.add(chosen_blocked_loc_id)

                target_wh = str(aisles_df.loc[aisle_mask, "warehouse_id"].iloc[0])
                order_wh_map = tables["orders"].set_index("order_id")["warehouse_id"].to_dict()
                wh_pick_indices = [
                    i for i, oid in enumerate(picks_df["order_id"].tolist()) if order_wh_map.get(oid) == target_wh
                ]
                for p_idx in wh_pick_indices[: inj.blocked_aisle_pick_count]:
                    pe_id = str(picks_df.at[p_idx, "pick_event_id"])
                    picks_df.at[p_idx, "location_id"] = chosen_blocked_loc_id
                    picks_df.at[p_idx, "event_status"] = PickEventStatus.BLOCKED_REROUTE.value
                    _add_gt(
                        issue_type=ExceptionIssueType.BLOCKED_AISLE_PICK_PATH,
                        category=ExceptionCategory.BUSINESS_RULE_VIOLATION,
                        severity=ExceptionSeverity.HIGH,
                        entity_type="PICK_EVENT",
                        entity_id=pe_id,
                        related_ids=[chosen_aisle_id, chosen_blocked_loc_id, str(picks_df.at[p_idx, "order_id"])],
                        valid_assignment=False,
                        rule_violated="Pick routes must never traverse an aisle where is_blocked=True.",
                        description=f"Pick event {pe_id} routed through blocked aisle {chosen_aisle_id} ({chosen_blocked_loc_id}).",
                    )

        # 5. REPLENISHMENT_LABOR_OVERLOAD
        if inj.replenishment_overload_warehouses > 0:
            target_whs = wh_df["warehouse_id"].tolist()[-inj.replenishment_overload_warehouses :]
            extra_reps: list[dict[str, Any]] = []
            for wid in target_whs:
                cap_min = float(wh_df.loc[wh_df["warehouse_id"] == wid, "daily_labor_minutes_capacity"].iloc[0])
                curr_min = float(
                    reps_df.loc[reps_df["warehouse_id"] == wid, "labor_minutes_required"].sum()
                    if not reps_df.empty
                    else 0.0
                )
                deficit_to_overload = max(180.0, (cap_min * 1.32) - curr_min)
                wh_assns = assn_df[assn_df["warehouse_id"] == wid].to_dict(orient="records")
                n_surge = min(6, max(2, len(wh_assns)))
                per_task_min = round(deficit_to_overload / float(n_surge), 2)
                surge_ids: list[str] = []

                for k in range(n_surge):
                    target_a = wh_assns[k % len(wh_assns)]
                    rep_id = format_replenishment_id(self.year, next_rep_seq)
                    next_rep_seq += 1
                    surge_ids.append(rep_id)
                    extra_reps.append(
                        {
                            "replenishment_id": rep_id,
                            "warehouse_id": wid,
                            "sku_id": target_a["sku_id"],
                            "source_location_id": None,
                            "destination_location_id": target_a["location_id"],
                            "required_quantity": 450,
                            "replenishment_quantity": 450,
                            "priority": ReplenishmentPriority.CRITICAL.value,
                            "labor_minutes_required": per_task_min,
                            "assigned_labor_minutes": round(per_task_min * 0.5, 2),
                            "replenishment_status": ReplenishmentStatus.OVERLOADED.value,
                            "created_at": self.snapshot_dt.isoformat(),
                            "completed_at": None,
                        }
                    )

                _add_gt(
                    issue_type=ExceptionIssueType.REPLENISHMENT_LABOR_OVERLOAD,
                    category=ExceptionCategory.OPERATIONAL_WARNING,
                    severity=ExceptionSeverity.HIGH,
                    entity_type="WAREHOUSE",
                    entity_id=wid,
                    related_ids=surge_ids,
                    valid_assignment=True,
                    rule_violated="Total warehouse replenishment labor_minutes_required exceeds daily_labor_minutes_capacity.",
                    description=f"Warehouse {wid} replenishment demand ({curr_min + per_task_min * n_surge:.1f} min) exceeds capacity ({cap_min:.1f} min).",
                )

            if extra_reps:
                reps_df = pd.concat([reps_df, pd.DataFrame(extra_reps)], ignore_index=True)
                tables["replenishments"] = reps_df

        # 6. NO_COMPATIBLE_LOCATION: oversized SKUs exceeding max facility slot dimensions (240x220x200)
        if inj.unassignable_oversized_sku_count > 0:
            picked_skus = set(picks_df["sku_id"].tolist()) if not picks_df.empty else set()
            rep_skus = set(reps_df["sku_id"].tolist()) if not reps_df.empty else set()
            # Prefer SKUs without pick/replenishment references, or tail slow movers
            candidate_skus = [
                sid
                for sid in skus_df["sku_id"].tolist()[::-1]
                if sid not in used_sku_ids and sid not in picked_skus and sid not in rep_skus
            ]
            if len(candidate_skus) < inj.unassignable_oversized_sku_count:
                fallback = [
                    sid
                    for sid in skus_df["sku_id"].tolist()[::-1]
                    if sid not in used_sku_ids and sid not in candidate_skus and sid not in rep_skus
                ]
                candidate_skus.extend(fallback)

            unassn_targets = candidate_skus[: inj.unassignable_oversized_sku_count]
            for sid in unassn_targets:
                # Remove any baseline assignment and clear its location occupancy cleanly
                existing_rows = assn_df[assn_df["sku_id"] == sid]
                for _, erow in existing_rows.iterrows():
                    elid = str(erow["location_id"])
                    if elid not in used_loc_ids:
                        el_idx = loc_idx_by_id[elid]
                        locs_df.at[el_idx, "current_weight_kg"] = 0.0
                        locs_df.at[el_idx, "current_volume_m3"] = 0.0
                if not picks_df.empty:
                    picks_df.drop(picks_df[picks_df["sku_id"] == sid].index, inplace=True)
                assn_df = assn_df[assn_df["sku_id"] != sid].copy()

                s_mask = skus_df["sku_id"] == sid
                skus_df.loc[s_mask, "width_cm"] = 325.0
                skus_df.loc[s_mask, "height_cm"] = 275.0
                skus_df.loc[s_mask, "depth_cm"] = 255.0
                skus_df.loc[s_mask, "storage_type"] = StorageType.OVERSIZE_FLOOR.value
                used_sku_ids.add(sid)

                _add_gt(
                    issue_type=ExceptionIssueType.NO_COMPATIBLE_LOCATION,
                    category=ExceptionCategory.OPTIMIZATION_FAILURE,
                    severity=ExceptionSeverity.HIGH,
                    entity_type="SKU",
                    entity_id=sid,
                    related_ids=[],
                    valid_assignment=False,
                    rule_violated="SKU dimensions must fit within at least one active compatible warehouse location.",
                    description=f"SKU {sid} dimensions (325x275x255 cm) exceed all warehouse storage locations.",
                )
            tables["slot_assignments"] = assn_df.reset_index(drop=True)
            tables["pick_events"] = picks_df.reset_index(drop=True)

        # 7. CAPACITY_NEARLY_EXHAUSTED (94-97% occupancy warning, strictly <= 100%)
        assn_df = tables["slot_assignments"]
        near_created = 0
        for a_idx in assn_df.index.tolist():
            if near_created >= inj.nearly_exhausted_location_count:
                break
            lid = str(assn_df.at[a_idx, "location_id"])
            sid = str(assn_df.at[a_idx, "sku_id"])
            if lid in used_loc_ids or sid in used_sku_ids:
                continue
            l_idx = loc_idx_by_id[lid]
            max_wt = float(locs_df.at[l_idx, "max_weight_kg"])
            max_vol = float(locs_df.at[l_idx, "max_volume_m3"])
            # Adjust unit weight / quantity cleanly so weight ratio lands at ~0.955 while volume stays <= 0.90
            u_wt = float(sku_lookup[sid]["unit_weight_kg"])
            u_vol = (
                float(sku_lookup[sid]["width_cm"])
                * float(sku_lookup[sid]["height_cm"])
                * float(sku_lookup[sid]["depth_cm"])
                / 1_000_000.0
            )
            qty = max(1, int(np.floor((max_wt * 0.955) / u_wt)))
            new_wt = round(qty * u_wt, 4)
            new_vol = round(qty * u_vol, 6)
            if new_wt / max_wt < 0.93 or new_wt > max_wt or new_vol > max_vol:
                continue

            assn_df.at[a_idx, "assigned_quantity"] = qty
            assn_df.at[a_idx, "allocated_weight_kg"] = new_wt
            assn_df.at[a_idx, "allocated_volume_m3"] = new_vol
            locs_df.at[l_idx, "current_weight_kg"] = new_wt
            locs_df.at[l_idx, "current_volume_m3"] = new_vol

            used_loc_ids.add(lid)
            used_sku_ids.add(sid)
            near_created += 1
            _add_gt(
                issue_type=ExceptionIssueType.CAPACITY_NEARLY_EXHAUSTED,
                category=ExceptionCategory.OPERATIONAL_WARNING,
                severity=ExceptionSeverity.MEDIUM,
                entity_type="LOCATION",
                entity_id=lid,
                related_ids=[str(assn_df.at[a_idx, "slot_assignment_id"]), sid],
                valid_assignment=True,
                rule_violated="Location utilization >= 92% near-exhaustion operational threshold.",
                description=f"Location {lid} at {(new_wt / max_wt) * 100:.1f}% weight utilization ({new_wt:.2f}/{max_wt:.2f} kg).",
            )

        _ = rng  # deterministic state preserved
        return pd.DataFrame(gt_rows)
