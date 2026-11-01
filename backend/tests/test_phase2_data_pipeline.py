"""Phase 2 automated test suite: DemandAnalytics, SyntheticDataGenerator, Validator, Loader, and Reproducibility."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from backend.app.config import load_domain_config
from backend.app.database import create_db_engine
from backend.app.services.data_generator import OPERATIONAL_TABLE_ORDER, SyntheticDataGenerator
from backend.app.services.data_loader import DatabaseLoader
from backend.app.services.demand_analytics import DemandAnalyticsService
from backend.app.validators.dataset_validator import FORBIDDEN_LEAKAGE_COLUMNS, DatasetValidator
from scripts.reproduce_dataset import verify_reproducibility


def test_demand_analytics_separates_abc_value_from_physical_velocity() -> None:
    """Verify ADV, ABC (revenue), XYZ (CV), and ROP formulas without confusing ABC and velocity."""
    service = DemandAnalyticsService()
    skus_df = pd.DataFrame(
        [
            {
                "sku_id": "SKU-000001",
                "unit_price": 2.00,
                "supplier_id": "SUP-0001",
            },  # High units, low revenue -> FAST, C
            {
                "sku_id": "SKU-000002",
                "unit_price": 5000.00,
                "supplier_id": "SUP-0001",
            },  # Low units, huge revenue -> SLOW/MED, A
            {"sku_id": "SKU-000003", "unit_price": 10.00, "supplier_id": "SUP-0001"},  # Zero demand -> SLOW, C, Z
        ]
    )
    suppliers_df = pd.DataFrame([{"supplier_id": "SUP-0001", "average_lead_time_days": 4.0}])
    orders_df = pd.DataFrame(
        [{"order_id": f"ORD-2026-{d + 1:06d}", "order_date": f"2026-01-{d + 1:02d}T10:00:00"} for d in range(10)]
    )
    lines = []
    # SKU-000001 ordered every single day (10 lines, 100 units total, $200 total value, CV = 0 -> X)
    for d in range(10):
        oid = f"ORD-2026-{d + 1:06d}"
        lines.append(
            {
                "order_line_id": f"OL-{oid}-001",
                "order_id": oid,
                "sku_id": "SKU-000001",
                "quantity": 10,
                "unit_price": 2.0,
                "line_value": 20.0,
            }
        )
    # SKU-000002 ordered once (1 line, 2 units, $10,000 value -> >98% of revenue -> Class A, yet low pick frequency!)
    lines.append(
        {
            "order_line_id": "OL-ORD-2026-000001-002",
            "order_id": "ORD-2026-000001",
            "sku_id": "SKU-000002",
            "quantity": 2,
            "unit_price": 5000.0,
            "line_value": 10000.0,
        }
    )
    order_lines_df = pd.DataFrame(lines)

    profiles = service.compute_profiles_dataframe(
        skus_df=skus_df,
        orders_df=orders_df,
        order_lines_df=order_lines_df,
        suppliers_df=suppliers_df,
        observation_start_date="2026-01-01",
        observation_days=10,
        effective_from=datetime(2026, 1, 11, 0, 0, 0),
    ).set_index("sku_id")

    # High physical volume / low dollar value item
    assert profiles.at["SKU-000001", "average_daily_demand"] == 10.0
    assert profiles.at["SKU-000001", "demand_std_dev"] == 0.0
    assert profiles.at["SKU-000001", "demand_velocity"] == "FAST"
    assert profiles.at["SKU-000001", "abc_class"] == "C"
    assert profiles.at["SKU-000001", "xyz_class"] == "X"
    assert profiles.at["SKU-000001", "reorder_point"] == 40.0  # 10 * 4 + 0

    # High dollar value / low physical frequency item
    assert profiles.at["SKU-000002", "abc_class"] == "A"
    assert profiles.at["SKU-000002", "demand_velocity"] == "MEDIUM"
    assert profiles.at["SKU-000002", "xyz_class"] == "Z"

    # Zero-demand item
    assert profiles.at["SKU-000003", "average_daily_demand"] == 0.0
    assert profiles.at["SKU-000003", "demand_velocity"] == "SLOW"
    assert profiles.at["SKU-000003", "abc_class"] == "C"
    assert profiles.at["SKU-000003", "xyz_class"] == "Z"


def test_clean_baseline_generation_has_zero_anomalies() -> None:
    """When anomaly injection is disabled, baseline generation must pass with 0 errors and 0 warnings."""
    cfg = load_domain_config("small")
    cfg.dataset.anomaly_injection.enabled = False
    generator = SyntheticDataGenerator(config=cfg, profile="small", seed=20261022)
    bundle = generator.generate()

    assert len(bundle.tables["warehouses"]) == 2
    assert len(bundle.tables["locations"]) == 500
    assert len(bundle.tables["skus"]) == 250
    assert len(bundle.tables["order_lines"]) == 5000
    assert bundle.ground_truth.empty

    validator = DatasetValidator(config=cfg, profile="small")
    report = validator.validate(bundle.tables, ground_truth_df=None)
    assert report["is_valid"] is True
    assert report["summary"]["unexpected_error_count"] == 0
    assert report["summary"]["operational_warning_count"] == 0
    assert report["summary"]["expected_labelled_anomaly_count"] == 0


def test_injected_anomalies_and_ground_truth_separation_and_negative_checks() -> None:
    """Verify all 7 injected anomaly categories reconcile 1-to-1 and negative corruptions fail validation."""
    cfg = load_domain_config("small")
    generator = SyntheticDataGenerator(config=cfg, profile="small", seed=20261022)
    bundle = generator.generate()

    # 1. Verify ground-truth isolation across all 16 operational tables
    for tbl_name in OPERATIONAL_TABLE_ORDER:
        cols_lower = {c.lower() for c in bundle.tables[tbl_name].columns}
        assert FORBIDDEN_LEAKAGE_COLUMNS.isdisjoint(cols_lower), f"Leakage column in {tbl_name}"

    expected_types = {
        "CAPACITY_EXCEEDED",
        "INCOMPATIBLE_COLOCATION",
        "DUPLICATE_ACTIVE_ASSIGNMENT",
        "BLOCKED_AISLE_PICK_PATH",
        "REPLENISHMENT_LABOR_OVERLOAD",
        "NO_COMPATIBLE_LOCATION",
        "CAPACITY_NEARLY_EXHAUSTED",
    }
    assert set(bundle.ground_truth["expected_issue_type"].unique()) == expected_types

    validator = DatasetValidator(config=cfg, profile="small")
    report = validator.validate(bundle.tables, ground_truth_df=bundle.ground_truth)
    assert report["is_valid"] is True
    assert report["summary"]["unexpected_error_count"] == 0
    assert report["summary"]["ground_truth_unmatched_total"] == 0

    # 2. Negative Test A: Leaked ground-truth column in operational table must fail check 18
    leaked_tables = {k: v.copy() for k, v in bundle.tables.items()}
    leaked_tables["skus"]["is_anomaly"] = False
    leak_report = validator.validate(leaked_tables, ground_truth_df=bundle.ground_truth)
    assert leak_report["is_valid"] is False
    assert leak_report["checks"]["18_GROUND_TRUTH_SEPARATION"] == "FAIL"

    # 3. Negative Test B: Orphan foreign key in zones must fail check 02
    orphan_tables = {k: v.copy() for k, v in bundle.tables.items()}
    orphan_tables["zones"].at[0, "warehouse_id"] = "WH-999"
    orphan_report = validator.validate(orphan_tables, ground_truth_df=bundle.ground_truth)
    assert orphan_report["is_valid"] is False
    assert orphan_report["checks"]["02_FK_INTEGRITY"] == "FAIL"

    # 4. Negative Test C: Corrupted order-line arithmetic must fail check 15
    math_tables = {k: v.copy() for k, v in bundle.tables.items()}
    math_tables["order_lines"].at[0, "line_value"] = 999999.99
    math_report = validator.validate(math_tables, ground_truth_df=bundle.ground_truth)
    assert math_report["is_valid"] is False
    assert math_report["checks"]["15_ORDER_LINE_ARITHMETIC"] == "FAIL"


def test_batch_database_loader_and_reproducibility(tmp_path: Path) -> None:
    """Verify CSV export -> SQLite batch load -> row count reconciliation and dual-run SHA-256 identity."""
    cfg = load_domain_config("small")
    gen = SyntheticDataGenerator(config=cfg, profile="small", seed=20261022)
    bundle = gen.generate()
    manifest = gen.export_bundle(bundle, output_root=tmp_path, export_sample=False)

    db_url = f"sqlite:///{(tmp_path / 'test_load.db').as_posix()}"
    engine = create_db_engine(db_url)
    loader = DatabaseLoader(engine=engine, batch_size=1000)
    stats = loader.load_profile_from_disk(profile="small", data_root=tmp_path, reset_existing=True)
    persisted = loader.verify_database_counts()

    assert persisted == manifest["entity_counts"]
    assert stats["total_records_inserted"] == sum(manifest["entity_counts"].values())
    engine.dispose()

    repro_report = verify_reproducibility(profile="small", seed=20261022)
    assert repro_report["is_reproducible"] is True
    assert repro_report["file_sha256_hashes_match"] is True
    assert repro_report["entity_counts_match"] is True
