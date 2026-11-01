"""Phase 1 verification tests: configuration loading, deterministic IDs, and 18-table ORM schema."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend.app.config import load_domain_config
from backend.app.database import Base, create_db_engine, init_db, session_scope
from backend.app.models import (
    SKU,
    Aisle,
    HazardClass,
    Location,
    Warehouse,
    Zone,
    ZoneHazardAllowance,
)
from backend.app.utils.ids import (
    format_aisle_id,
    format_location_id,
    format_optimization_run_id,
    format_order_id,
    format_pick_event_id,
    format_replenishment_id,
    format_sku_id,
    format_slot_assignment_id,
    format_warehouse_id,
    format_zone_id,
    validate_id_format,
)


def test_yaml_profiles_load_and_validate() -> None:
    small = load_domain_config("small")
    medium = load_domain_config("medium")
    full = load_domain_config("full")

    assert small.project.seed == 20261022
    assert (small.dataset.warehouses, small.dataset.locations, small.dataset.skus, small.dataset.order_lines) == (
        2,
        500,
        250,
        5000,
    )
    assert (
        medium.dataset.warehouses,
        medium.dataset.locations,
        medium.dataset.skus,
        medium.dataset.order_lines,
    ) == (
        5,
        2500,
        1000,
        25000,
    )
    assert (full.dataset.warehouses, full.dataset.locations, full.dataset.skus, full.dataset.order_lines) == (
        20,
        10000,
        5000,
        100000,
    )


def test_deterministic_id_conventions() -> None:
    wh_id = format_warehouse_id(1)
    zone_id = format_zone_id(1)
    loc_id = format_location_id(zone_id, 1)
    sku_id = format_sku_id(1)
    ord_id = format_order_id(2026, 1)
    pick_id = format_pick_event_id(ord_id, 1)
    slot_id = format_slot_assignment_id(2026, 1)
    rep_id = format_replenishment_id(2026, 1)
    opt_id = format_optimization_run_id(2026, 1)

    assert wh_id == "WH-001" and validate_id_format("warehouse_id", wh_id)
    assert loc_id == "LOC-Z001-000001" and validate_id_format("location_id", loc_id)
    assert sku_id == "SKU-000001" and validate_id_format("sku_id", sku_id)
    assert ord_id == "ORD-2026-000001" and validate_id_format("order_id", ord_id)
    assert pick_id == "PICK-ORD-2026-000001-001" and validate_id_format("pick_event_id", pick_id)
    assert slot_id == "SLOT-2026-000001" and validate_id_format("slot_assignment_id", slot_id)
    assert rep_id == "REP-2026-000001" and validate_id_format("replenishment_id", rep_id)
    assert opt_id == "OPT-2026-000001" and validate_id_format("optimization_run_id", opt_id)


def test_schema_creation_and_foreign_key_enforcement() -> None:
    engine = create_db_engine("sqlite:///:memory:")
    init_db(engine=engine)

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    expected_tables = {
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
        "optimization_runs",
        "slot_assignments",
        "assignment_reasons",
        "scenarios",
        "exceptions",
        "audit_events",
    }
    assert expected_tables.issubset(table_names)
    assert len(Base.metadata.tables) == 21

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    ref_time = datetime(2026, 4, 1, 0, 0, 0)

    with session_scope(factory) as session:
        fk_enabled = session.execute(text("PRAGMA foreign_keys")).scalar()
        assert fk_enabled == 1

        hz = HazardClass(
            hazard_class_id="NONE",
            class_name="Non-Hazardous",
            requires_segregation=False,
            severity_level=0,
            description="Standard non-hazardous merchandise",
        )
        wh = Warehouse(
            warehouse_id="WH-001",
            warehouse_name="Central Fulfillment DC",
            warehouse_type="FULFILLMENT_CENTER",
            city="Pune",
            state="Maharashtra",
            country="India",
            total_area_sqft=120000.0,
            operating_hours="24/7",
            daily_labor_minutes_capacity=3600.0,
            status="ACTIVE",
            created_at=ref_time,
        )
        zone = Zone(
            zone_id="Z001",
            warehouse_id="WH-001",
            zone_name="Forward Fast Pick",
            zone_type="FAST_PICK",
            temperature_type="AMBIENT",
            allowed_hazard_classes="NONE",
            max_weight_kg=50000.0,
            max_volume_m3=400.0,
            status="ACTIVE",
        )
        bridge = ZoneHazardAllowance(zone_id="Z001", hazard_class_id="NONE")
        aisle = Aisle(
            aisle_id=format_aisle_id("Z001", 1),
            zone_id="Z001",
            warehouse_id="WH-001",
            aisle_number=1,
            x_start=5.0,
            y_start=2.0,
            x_end=5.0,
            y_end=42.0,
            width_m=3.2,
            is_blocked=False,
            congestion_factor=1.0,
            status="ACTIVE",
        )
        loc = Location(
            location_id="LOC-Z001-000001",
            zone_id="Z001",
            aisle_id="AISLE-Z001-01",
            rack_id="R01",
            shelf_level=2,
            bin_id="B01",
            x_coordinate=5.0,
            y_coordinate=6.0,
            width_cm=100.0,
            height_cm=80.0,
            depth_cm=100.0,
            max_weight_kg=250.0,
            max_volume_m3=0.80,
            current_weight_kg=0.0,
            current_volume_m3=0.0,
            accessibility_type="GOLDEN_ZONE",
            storage_policy="DEDICATED_SINGLE_SKU",
            is_blocked=False,
            status="ACTIVE",
        )
        sku = SKU(
            sku_id="SKU-000001",
            sku_name="Precision Sensor Module",
            category="ELECTRONICS",
            unit_price=49.99,
            unit_weight_kg=1.25,
            width_cm=20.0,
            height_cm=15.0,
            depth_cm=10.0,
            is_fragile=True,
            hazard_class="NONE",
            temperature_requirement="AMBIENT",
            storage_type="BIN",
            supplier_id=None,
            status="ACTIVE",
        )
        session.add_all([hz, wh, zone, bridge, aisle, loc, sku])

    # Verify negative FK constraint rejection (orphan zone referencing non-existent warehouse)
    with pytest.raises(IntegrityError), session_scope(factory) as session:
        session.add(
            Zone(
                zone_id="Z999",
                warehouse_id="WH-999",
                zone_name="Invalid Orphan Zone",
                zone_type="GENERAL",
                temperature_type="AMBIENT",
                allowed_hazard_classes="NONE",
                max_weight_kg=1000.0,
                max_volume_m3=10.0,
                status="ACTIVE",
            )
        )
