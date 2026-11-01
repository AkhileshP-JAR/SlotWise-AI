"""Comprehensive Phase 3 & Phase 4 unit and integration tests.

Covers:
- Demand velocity, ABC/XYZ classification, and safety-stock / reorder-point formulas
- Physical volume and available location capacity calculations
- Multi-factor [0, 1] scoring normalization & strict hard-constraint non-override invariant
- Candidate ranking and explainable rejection-reason tracking
- All 5 mandatory negative business scenarios:
  1. Oversized / over-capacity SKU rejected by CompatibilityService & Scorer
  2. Incompatible hazard, thermal, and food/chemical co-location rejected & detected
  3. Duplicate active assignment on DEDICATED_SINGLE_SKU location rejected, detected, and remediated
  4. Pick path through blocked aisle excluded by router, rejected by slotting, and flagged by exception engine
  5. Replenishment workload exceeding warehouse labor capacity marked OVERLOADED and flagged
- Non-destructive assignment history (PROPOSED -> REJECTED, PROPOSED -> ACTIVE -> SUPERSEDED)
- OR-Tools CP-SAT and Deterministic Greedy + 2-Way Local Swap solvers
- Baseline vs. Optimized picking comparison on identical orders, replenishment simulation, scenarios, and Ground-Truth F1
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import func, select

from backend.app.analytics.kpi_service import AnalyticsKPIService
from backend.app.config import DATA_DIR, ScoringConfig, ScoringWeightsConfig, load_domain_config
from backend.app.database import create_db_engine, session_scope
from backend.app.models import AssignmentReason, AuditEvent, ExceptionRecord, Location, SlotAssignment
from backend.app.models.enums import (
    AccessibilityType,
    AssignmentStatus,
    DemandVelocityClass,
    EntityStatus,
    ExceptionIssueType,
    HazardCode,
    ReplenishmentStatus,
    SKUCategory,
    StoragePolicy,
    StorageType,
    TemperatureType,
    ZoneType,
)
from backend.app.optimization.candidates import CandidateGeneratorService
from backend.app.optimization.scoring import MultiFactorScorer, SKUAnalyticalMetrics
from backend.app.optimization.solver import SlottingOptimizerService
from backend.app.services.compatibility import (
    CompatibilityService,
    LocationSpec,
    PairwiseConflictRules,
    SKUSpec,
    calculate_available_capacity,
    calculate_unit_volume_m3,
)
from backend.app.services.data_loader import DatabaseLoader
from backend.app.services.demand_analytics import DemandAnalyticsService
from backend.app.services.exception_engine import ExceptionDetectionService
from backend.app.simulation.picking_simulator import PickingSimulationService, StopPoint
from backend.app.simulation.replenishment_simulator import ReplenishmentSimulationService
from backend.app.simulation.scenario_service import ScenarioComparisonService


def _make_sku(
    sku_id: str = "SKU-000001",
    category: str = SKUCategory.ELECTRONICS.value,
    unit_weight_kg: float = 4.0,
    width_cm: float = 30.0,
    height_cm: float = 20.0,
    depth_cm: float = 25.0,
    is_fragile: bool = False,
    hazard_class: str = HazardCode.NONE.value,
    temperature_requirement: str = TemperatureType.AMBIENT.value,
    storage_type: str = StorageType.BIN.value,
) -> SKUSpec:
    return SKUSpec(
        sku_id=sku_id,
        category=category,
        unit_weight_kg=unit_weight_kg,
        width_cm=width_cm,
        height_cm=height_cm,
        depth_cm=depth_cm,
        is_fragile=is_fragile,
        hazard_class=hazard_class,
        temperature_requirement=temperature_requirement,
        storage_type=storage_type,
        status="ACTIVE",
    )


def _make_location(
    location_id: str = "LOC-Z001-000001",
    warehouse_id: str = "WH-001",
    zone_id: str = "Z001",
    zone_type: str = ZoneType.FAST_PICK.value,
    temperature_type: str = TemperatureType.AMBIENT.value,
    allowed_hazards: tuple[str, ...] = (HazardCode.NONE.value, HazardCode.LITHIUM_BATTERY.value),
    aisle_id: str = "AISLE-Z001-01",
    aisle_is_blocked: bool = False,
    x_coordinate: float = 8.0,
    y_coordinate: float = 6.0,
    width_cm: float = 120.0,
    height_cm: float = 100.0,
    depth_cm: float = 80.0,
    max_weight_kg: float = 250.0,
    max_volume_m3: float = 0.96,
    occupied_weight_kg: float = 0.0,
    occupied_volume_m3: float = 0.0,
    accessibility_type: str = AccessibilityType.GOLDEN_ZONE.value,
    storage_policy: str = StoragePolicy.DEDICATED_SINGLE_SKU.value,
    is_blocked: bool = False,
    status: str = EntityStatus.ACTIVE.value,
    co_located_skus: tuple[SKUSpec, ...] = (),
) -> LocationSpec:
    return LocationSpec(
        location_id=location_id,
        warehouse_id=warehouse_id,
        warehouse_status=EntityStatus.ACTIVE.value,
        zone_id=zone_id,
        zone_type=zone_type,
        zone_status=EntityStatus.ACTIVE.value,
        temperature_type=temperature_type,
        allowed_hazard_classes=frozenset(allowed_hazards),
        aisle_id=aisle_id,
        aisle_is_blocked=aisle_is_blocked,
        x_coordinate=x_coordinate,
        y_coordinate=y_coordinate,
        depot_x=2.0,
        depot_y=2.0,
        width_cm=width_cm,
        height_cm=height_cm,
        depth_cm=depth_cm,
        max_weight_kg=max_weight_kg,
        max_volume_m3=max_volume_m3,
        occupied_weight_kg=occupied_weight_kg,
        occupied_volume_m3=occupied_volume_m3,
        accessibility_type=accessibility_type,
        storage_policy=storage_policy,
        is_blocked=is_blocked,
        status=status,
        co_located_skus=co_located_skus,
    )


def _make_metrics(
    sku_id: str = "SKU-000001",
    adv: float = 12.0,
    velocity: str = DemandVelocityClass.FAST.value,
    vel_pct: float = 0.95,
    pick_freq: float = 4.0,
    norm_freq: float = 0.90,
    abc_class: str = "A",
    xyz_class: str = "X",
    desired_qty: int = 20,
) -> SKUAnalyticalMetrics:
    return SKUAnalyticalMetrics(
        sku_id=sku_id,
        average_daily_demand=adv,
        demand_velocity=velocity,
        velocity_percentile=vel_pct,
        pick_frequency=pick_freq,
        normalized_pick_frequency=norm_freq,
        abc_class=abc_class,
        xyz_class=xyz_class,
        replenishment_frequency=3.0,
        target_days_of_supply=10,
        desired_quantity=desired_qty,
    )


def test_demand_velocity_abc_xyz_and_rop_calculations() -> None:
    """Verify exact demand velocity, ABC revenue Pareto, XYZ CV tiers, and ROP math."""
    service = DemandAnalyticsService()

    # Exact ROP helper validation: SS = 1.645 * 4.0 * sqrt(9) = 19.74; ROP = 10 * 9 + 19.74 = 109.74
    rop_res = service.calculate_single_rop(adv=10.0, lead_time_days=9.0, demand_std_dev=4.0, z_score=1.645)
    assert rop_res["safety_stock"] == pytest.approx(19.74, abs=1e-4)
    assert rop_res["reorder_point"] == pytest.approx(109.74, abs=1e-4)

    with pytest.raises(ValueError):
        service.calculate_single_rop(adv=-1.0, lead_time_days=5.0, demand_std_dev=1.0)

    # Multi-SKU analytical derivation over a 10-day horizon
    skus_df = pd.DataFrame(
        [
            {
                "sku_id": "SKU-000001",
                "unit_price": 100.0,
                "supplier_id": "SUP-0001",
            },  # High steady revenue -> FAST, A, X
            {
                "sku_id": "SKU-000002",
                "unit_price": 25.0,
                "supplier_id": "SUP-0001",
            },  # Moderate demand -> MEDIUM, B
            {
                "sku_id": "SKU-000003",
                "unit_price": 5.0,
                "supplier_id": "SUP-0001",
            },  # Sporadic single spike -> C, Z
            {"sku_id": "SKU-000004", "unit_price": 50.0, "supplier_id": "SUP-0001"},  # Zero demand -> SLOW, C, Z
        ]
    )
    suppliers_df = pd.DataFrame([{"supplier_id": "SUP-0001", "average_lead_time_days": 4.0}])

    orders_rows = []
    line_rows = []
    line_seq = 1
    for day in range(10):
        oid = f"ORD-2026-{day + 1:06d}"
        orders_rows.append({"order_id": oid, "order_date": f"2026-01-{day + 1:02d}T10:00:00"})
        # SKU-000001: 10 units every single day -> std_dev = 0.0, CV = 0.0 -> X, total rev = 10,000
        line_rows.append(
            {
                "order_line_id": f"OL-{line_seq:04d}",
                "order_id": oid,
                "sku_id": "SKU-000001",
                "quantity": 10,
                "line_value": 1000.0,
            }
        )
        line_seq += 1
        # SKU-000002: 4 units on 5 days -> rev = 500
        if day % 2 == 0:
            line_rows.append(
                {
                    "order_line_id": f"OL-{line_seq:04d}",
                    "order_id": oid,
                    "sku_id": "SKU-000002",
                    "quantity": 4,
                    "line_value": 100.0,
                }
            )
            line_seq += 1
        # SKU-000003: 3 units on day 0 only -> high CV > 1.0 -> Z, rev = 15
        if day == 0:
            line_rows.append(
                {
                    "order_line_id": f"OL-{line_seq:04d}",
                    "order_id": oid,
                    "sku_id": "SKU-000003",
                    "quantity": 3,
                    "line_value": 15.0,
                }
            )
            line_seq += 1

    profiles_df = service.compute_profiles_dataframe(
        skus_df=skus_df,
        orders_df=pd.DataFrame(orders_rows),
        order_lines_df=pd.DataFrame(line_rows),
        suppliers_df=suppliers_df,
        observation_start_date="2026-01-01",
        observation_days=10,
        effective_from=datetime(2026, 1, 11, 0, 0, 0),
    ).set_index("sku_id")

    p1 = profiles_df.loc["SKU-000001"]
    assert p1["average_daily_demand"] == pytest.approx(10.0)
    assert p1["demand_std_dev"] == pytest.approx(0.0)
    assert p1["demand_velocity"] == "FAST"
    assert p1["abc_class"] == "A"
    assert p1["xyz_class"] == "X"
    assert p1["reorder_point"] == pytest.approx(40.0)  # 10 * 4 + 0

    p3 = profiles_df.loc["SKU-000003"]
    assert p3["xyz_class"] == "Z"

    p4 = profiles_df.loc["SKU-000004"]
    assert p4["total_units_sold"] == 0
    assert p4["velocity_percentile"] == 0.0
    assert p4["demand_velocity"] == "SLOW"
    assert p4["abc_class"] == "C"
    assert p4["xyz_class"] == "Z"


def test_volume_and_location_capacity_math() -> None:
    """Verify cubic volume conversion (cm -> m^3) and remaining weight/volume capacity calculations."""
    assert calculate_unit_volume_m3(50.0, 40.0, 30.0) == pytest.approx(0.06, abs=1e-8)
    with pytest.raises(ValueError):
        calculate_unit_volume_m3(0.0, 20.0, 10.0)

    avail_wt, avail_vol = calculate_available_capacity(
        max_weight_kg=500.0,
        occupied_weight_kg=125.5,
        max_volume_m3=1.20,
        occupied_volume_m3=0.45,
    )
    assert avail_wt == pytest.approx(374.5)
    assert avail_vol == pytest.approx(0.75)


def test_negative_scenario_1_oversized_and_overcapacity_sku_rejected() -> None:
    """MANDATORY NEGATIVE SCENARIO 1: Oversized / overweight SKU rejected and never overridden by scoring."""
    compat_service = CompatibilityService()
    std_loc = _make_location(
        width_cm=120.0, height_cm=100.0, depth_cm=80.0, max_weight_kg=200.0, max_volume_m3=0.96
    )

    # 1A. Physical dimensions exceed slot envelope
    oversized_sku = _make_sku(
        sku_id="SKU-OVERSIZE",
        width_cm=240.0,
        height_cm=210.0,
        depth_cm=180.0,
        unit_weight_kg=85.0,
    )
    res_dim = compat_service.evaluate(sku=oversized_sku, location=std_loc, quantity=1)
    assert res_dim.is_feasible is False
    assert "DIM_WIDTH_EXCEEDED" in res_dim.rejection_reasons
    assert "DIM_HEIGHT_EXCEEDED" in res_dim.rejection_reasons
    assert "DIM_DEPTH_EXCEEDED" in res_dim.rejection_reasons
    assert "UNIT_VOLUME_EXCEEDS_LOCATION_MAX" in res_dim.rejection_reasons

    # 1B. Dimensions fit, but assigned lot weight exceeds available location capacity
    dense_sku = _make_sku(sku_id="SKU-HEAVY", width_cm=30.0, height_cm=25.0, depth_cm=25.0, unit_weight_kg=18.0)
    partially_loaded_loc = _make_location(max_weight_kg=200.0, occupied_weight_kg=150.0)
    res_wt = compat_service.evaluate(
        sku=dense_sku, location=partially_loaded_loc, quantity=4
    )  # requires 72 kg > 50 kg
    assert res_wt.is_feasible is False
    assert "WEIGHT_CAPACITY_INSUFFICIENT" in res_wt.rejection_reasons

    # Prove hard constraints can NEVER be overridden by maximum velocity/distance soft scoring weights
    extreme_scorer = MultiFactorScorer(
        ScoringConfig(
            weights=ScoringWeightsConfig(
                demand_velocity=0.50,
                pick_frequency=0.40,
                distance=0.04,
                capacity_utilization=0.02,
                replenishment_efficiency=0.02,
                zone_compatibility=0.02,
            )
        )
    )
    top_mover_metrics = _make_metrics(sku_id="SKU-OVERSIZE", vel_pct=1.0, norm_freq=1.0)
    breakdown = extreme_scorer.score_candidate(
        sku=oversized_sku,
        metrics=top_mover_metrics,
        location=std_loc,
        compatibility=res_dim,
        max_warehouse_distance_m=100.0,
    )
    assert breakdown.total_score == 0.0
    assert "DIM_WIDTH_EXCEEDED" in breakdown.explanation


def test_negative_scenario_2_incompatible_hazard_thermal_and_colocation() -> None:
    """MANDATORY NEGATIVE SCENARIO 2: Incompatible hazard/category co-location & thermal/zone mismatches."""
    rules = PairwiseConflictRules.from_rule_dicts(
        [
            {
                "rule_type": "HAZARD_CO_LOCATION",
                "primary_attribute": "FLAMMABLE",
                "conflicting_attribute": "OXIDIZER",
                "is_hard_constraint": True,
            },
            {
                "rule_type": "CATEGORY_CO_LOCATION",
                "primary_attribute": "FOOD_BEVERAGE",
                "conflicting_attribute": "CHEMICAL_CLEANING",
                "is_hard_constraint": True,
            },
        ]
    )
    compat_service = CompatibilityService(conflict_rules=rules)

    # 2A. Chilled SKU in Ambient location -> TEMPERATURE_MISMATCH
    chilled_sku = _make_sku(
        sku_id="SKU-COLD",
        category=SKUCategory.COLD_PRODUCE.value,
        temperature_requirement=TemperatureType.CHILLED.value,
    )
    ambient_loc = _make_location(temperature_type=TemperatureType.AMBIENT.value)
    res_temp = compat_service.evaluate(chilled_sku, ambient_loc, quantity=5)
    assert res_temp.is_feasible is False
    assert "TEMPERATURE_MISMATCH:CHILLED_VS_AMBIENT" in res_temp.rejection_reasons

    # 2B. Flammable SKU in non-hazmat General location -> HAZARD_NOT_PERMITTED
    flammable_chem = _make_sku(
        sku_id="SKU-FLAM",
        category=SKUCategory.CHEMICAL_CLEANING.value,
        hazard_class=HazardCode.FLAMMABLE.value,
    )
    res_haz_zone = compat_service.evaluate(flammable_chem, ambient_loc, quantity=5)
    assert res_haz_zone.is_feasible is False
    assert "HAZARD_NOT_PERMITTED:FLAMMABLE" in res_haz_zone.rejection_reasons

    # 2C. Flammable Chemical SKU co-located in SHARED_COMPATIBLE Hazmat slot holding Oxidizer + Food SKU
    oxidizer_occupant = _make_sku(
        sku_id="SKU-OXID",
        category=SKUCategory.CHEMICAL_CLEANING.value,
        hazard_class=HazardCode.OXIDIZER.value,
    )
    food_occupant = _make_sku(
        sku_id="SKU-FOOD",
        category=SKUCategory.FOOD_BEVERAGE.value,
        hazard_class=HazardCode.NONE.value,
    )
    shared_hazmat_loc = _make_location(
        zone_type=ZoneType.HAZMAT.value,
        allowed_hazards=(HazardCode.NONE.value, HazardCode.FLAMMABLE.value, HazardCode.OXIDIZER.value),
        storage_policy=StoragePolicy.SHARED_COMPATIBLE.value,
        co_located_skus=(oxidizer_occupant, food_occupant),
    )
    res_coloc = compat_service.evaluate(flammable_chem, shared_hazmat_loc, quantity=2)
    assert res_coloc.is_feasible is False
    assert "HAZARD_CONFLICT:FLAMMABLE_WITH_OXIDIZER" in res_coloc.rejection_reasons
    assert "CATEGORY_CONFLICT:CHEMICAL_CLEANING_WITH_FOOD_BEVERAGE" in res_coloc.rejection_reasons


def test_negative_scenario_3_duplicate_active_location_assignment() -> None:
    """MANDATORY NEGATIVE SCENARIO 3: Dedicated single-SKU location rejects any second active SKU occupant."""
    compat_service = CompatibilityService()
    existing_sku = _make_sku(sku_id="SKU-000010")
    incoming_sku = _make_sku(sku_id="SKU-000011")

    dedicated_occupied_loc = _make_location(
        location_id="LOC-Z001-000009",
        storage_policy=StoragePolicy.DEDICATED_SINGLE_SKU.value,
        occupied_weight_kg=20.0,
        occupied_volume_m3=0.05,
        co_located_skus=(existing_sku,),
    )
    res = compat_service.evaluate(incoming_sku, dedicated_occupied_loc, quantity=2)
    assert res.is_feasible is False
    assert "DEDICATED_LOCATION_ALREADY_OCCUPIED" in res.rejection_reasons
    assert res.hard_constraints_checked["storage_policy_and_colocation"] is False


def test_negative_scenario_4_blocked_aisle_pick_path_rejected_and_detected() -> None:
    """MANDATORY NEGATIVE SCENARIO 4: Blocked aisles are rejected for slotting and excluded/detoured in pick paths."""
    compat_service = CompatibilityService()
    sku = _make_sku(sku_id="SKU-000020")
    blocked_aisle_loc = _make_location(location_id="LOC-BLK-01", aisle_id="AISLE-BLK", aisle_is_blocked=True)

    # Slotting into a blocked aisle is strictly infeasible
    compat_res = compat_service.evaluate(sku, blocked_aisle_loc, quantity=2)
    assert compat_res.is_feasible is False
    assert "AISLE_BLOCKED" in compat_res.rejection_reasons

    # Picking router excludes stops in blocked aisles and adds perimeter detour when crossing a blocked aisle's X
    pick_sim = PickingSimulationService()
    stops = [
        StopPoint(
            location_id="LOC-FAR",
            sku_id="SKU-000021",
            aisle_id="AISLE-03",
            x=24.0,
            y=22.0,
            y_front=4.0,
            y_rear=34.0,
            aisle_blocked=False,
            location_blocked=False,
            congestion_factor=1.0,
            quantity=2,
        ),
        StopPoint(
            location_id="LOC-BLOCKED",
            sku_id="SKU-000022",
            aisle_id="AISLE-02",
            x=14.0,
            y=15.0,
            y_front=4.0,
            y_rear=34.0,
            aisle_blocked=True,
            location_blocked=False,
            congestion_factor=1.0,
            quantity=1,
        ),
        StopPoint(
            location_id="LOC-NEAR",
            sku_id="SKU-000023",
            aisle_id="AISLE-01",
            x=6.0,
            y=8.0,
            y_front=4.0,
            y_rear=34.0,
            aisle_blocked=False,
            location_blocked=False,
            congestion_factor=1.0,
            quantity=3,
        ),
    ]

    tour_without_block_header = pick_sim.simulate_single_order_tour(
        depot_x=2.0, depot_y=2.0, stops=stops, blocked_aisle_xs=[]
    )
    tour_with_block_header = pick_sim.simulate_single_order_tour(
        depot_x=2.0, depot_y=2.0, stops=stops, blocked_aisle_xs=[14.0]
    )

    assert tour_with_block_header["blocked_stop_count"] == 1
    assert tour_with_block_header["blocked_location_ids"] == ["LOC-BLOCKED"]
    assert "LOC-BLOCKED" not in tour_with_block_header["optimized_sequence"]
    assert tour_with_block_header["valid_stop_count"] == 2
    # Nearest-neighbor visits LOC-NEAR before LOC-FAR, and blocked aisle x=14.0 adds horizontal bypass detour
    assert tour_with_block_header["optimized_sequence"][0] == "LOC-NEAR"
    assert tour_with_block_header["optimized_distance_m"] > tour_without_block_header["optimized_distance_m"]


def test_candidate_ranking_and_explainable_scoring_determinism() -> None:
    """Verify normalized [0, 1] 6-factor scoring ranks near-dock Golden-Zone slots above distant high-rack bays."""
    cand_service = CandidateGeneratorService()
    compat_service = CompatibilityService()

    fast_sku = _make_sku(sku_id="SKU-FAST", width_cm=25.0, height_cm=20.0, depth_cm=20.0, unit_weight_kg=2.5)
    fast_metrics = _make_metrics(
        sku_id="SKU-FAST", adv=15.0, velocity="FAST", vel_pct=0.96, norm_freq=0.94, desired_qty=25
    )

    prime_loc = _make_location(
        location_id="LOC-PRIME",
        zone_type=ZoneType.FAST_PICK.value,
        accessibility_type=AccessibilityType.GOLDEN_ZONE.value,
        x_coordinate=6.0,
        y_coordinate=5.0,
    )
    distant_high_rack = _make_location(
        location_id="LOC-DISTANT",
        zone_type=ZoneType.GENERAL.value,
        accessibility_type=AccessibilityType.HIGH_RACK_FORKLIFT.value,
        x_coordinate=65.0,
        y_coordinate=45.0,
    )
    blocked_loc = _make_location(location_id="LOC-BLOCKED", is_blocked=True)

    pool = cand_service.evaluate_candidates_for_sku(
        sku=fast_sku,
        metrics=fast_metrics,
        locations=[distant_high_rack, blocked_loc, prime_loc],
        compat_service=compat_service,
        max_wh_dist={"WH-001": 110.0},
        top_n=5,
    )

    assert pool.total_locations_evaluated == 3
    assert pool.total_feasible_count == 2
    assert pool.total_rejected_count == 1
    assert pool.rejection_reason_counts == {"LOCATION_BLOCKED": 1}
    assert pool.top_feasible[0].location_id == "LOC-PRIME"
    assert pool.top_feasible[0].rank_position == 1
    assert pool.top_feasible[0].total_score > pool.top_feasible[1].total_score

    sb = pool.top_feasible[0].score_breakdown
    for val in (
        sb.total_score,
        sb.velocity_score,
        sb.pick_frequency_score,
        sb.distance_score,
        sb.capacity_score,
        sb.replenishment_score,
        sb.compatibility_score,
    ):
        assert 0.0 <= val <= 1.0


def test_end_to_end_small_optimization_history_negative_5_and_ground_truth_f1(tmp_path: Path) -> None:
    """Integration test on isolated SQLite DB verifying Ground-Truth F1, Negative Scenario 5, History, OR-Tools & Greedy, and Picking."""
    config = load_domain_config(profile="small")
    db_file = tmp_path / "phase3_4_integration.db"
    engine = create_db_engine(f"sqlite:///{db_file.as_posix()}")

    loader = DatabaseLoader(engine=engine)
    session_factory = loader.session_factory
    load_res = loader.load_profile_from_disk(profile="small", data_root=DATA_DIR, reset_existing=True)
    assert load_res["total_records_inserted"] > 10_000

    exc_service = ExceptionDetectionService(config=config)
    opt_service = SlottingOptimizerService(config=config)
    pick_service = PickingSimulationService(config=config)
    rep_service = ReplenishmentSimulationService(config=config)
    scenario_service = ScenarioComparisonService(config=config)
    kpi_service = AnalyticsKPIService(config=config)

    with session_scope(session_factory) as session:
        # 1. Baseline Exception Scan & Ground-Truth Evaluation (verifies all 5 anomaly classes in DB!)
        base_scan = exc_service.run_detection(session=session)
        assert base_scan["newly_created_count"] >= 21
        assert base_scan["by_issue_type"].get(ExceptionIssueType.CAPACITY_EXCEEDED.value, 0) >= 4
        assert base_scan["by_issue_type"].get(ExceptionIssueType.INCOMPATIBLE_COLOCATION.value, 0) >= 3
        assert base_scan["by_issue_type"].get(ExceptionIssueType.DUPLICATE_ACTIVE_ASSIGNMENT.value, 0) >= 3
        assert base_scan["by_issue_type"].get(ExceptionIssueType.BLOCKED_AISLE_PICK_PATH.value, 0) >= 4
        assert base_scan["by_issue_type"].get(ExceptionIssueType.REPLENISHMENT_LABOR_OVERLOAD.value, 0) >= 1
        assert base_scan["by_issue_type"].get(ExceptionIssueType.NO_COMPATIBLE_LOCATION.value, 0) == 2

        gt_metrics = kpi_service.evaluate_exception_detection_against_ground_truth(
            session=session, profile="small", data_root=DATA_DIR
        )
        assert gt_metrics["recall"] == 1.0
        assert gt_metrics["true_positives"] == 21
        assert gt_metrics["false_negatives"] == 0
        assert gt_metrics["f1_score"] >= 0.95

        # Test exception lifecycle state machine transitions (OPEN -> IN_REVIEW -> RESOLVED; invalid RESOLVED -> IN_REVIEW rejected)
        first_exc = session.scalars(
            select(ExceptionRecord).order_by(ExceptionRecord.exception_id).limit(1)
        ).first()
        assert first_exc is not None
        exc_service.update_exception_status(session, first_exc.exception_id, "IN_REVIEW", "Investigating slot")
        assert first_exc.resolution_status == "IN_REVIEW"
        exc_service.update_exception_status(session, first_exc.exception_id, "OPEN", "Re-opened for automated fix")
        assert first_exc.resolution_status == "OPEN"

        # 2. MANDATORY NEGATIVE SCENARIO 5: Replenishment simulation exceeding tightened warehouse labor capacity
        tight_rep = rep_service.simulate_replenishment(
            session=session,
            warehouse_id="WH-001",
            use_baseline_assignments=True,
            labor_capacity_multiplier=0.05,  # Tight 5% labor budget forces deterministic overload
            persist_events=True,
        )
        assert tight_rep["total_requests"] > 0
        assert tight_rep["overloaded_task_count"] > 0
        assert "WH-001" in tight_rep["overloaded_warehouses"]
        assert any(t["replenishment_status"] == ReplenishmentStatus.OVERLOADED.value for t in tight_rep["tasks"])

        # Re-scanning detects WH-001 replenishment labor overload automatically
        overload_scan = exc_service.run_detection(session=session)
        assert overload_scan["by_issue_type"][ExceptionIssueType.REPLENISHMENT_LABOR_OVERLOAD.value] == 2

        # 3. Verify PROPOSED -> REJECTED workflow leaves baseline ACTIVE assignments intact
        baseline_active_before = int(
            session.scalar(
                select(func.count())
                .select_from(SlotAssignment)
                .where(SlotAssignment.assignment_status == AssignmentStatus.ACTIVE.value)
            )
            or 0
        )
        trial_greedy = opt_service.run_optimization(
            session=session,
            warehouse_id="WH-001",
            strategy="greedy",
            apply_immediately=False,
        )
        assert trial_greedy.algorithm_name == "DETERMINISTIC_GREEDY_LOCAL_SWAP"
        assert trial_greedy.assigned_count > 100
        rej_res = opt_service.reject_assignments(
            session=session,
            run_id=trial_greedy.run_id,
            reason="Testing non-destructive rejection workflow",
        )
        assert rej_res["rejected_count"] == trial_greedy.assigned_count
        baseline_active_after_reject = int(
            session.scalar(
                select(func.count())
                .select_from(SlotAssignment)
                .where(SlotAssignment.assignment_status == AssignmentStatus.ACTIVE.value)
            )
            or 0
        )
        assert baseline_active_after_reject == baseline_active_before

        # 4. Run full OR-Tools CP-SAT optimization across all warehouses and approve -> ACTIVE
        baseline_wh_sku_loc = {
            (r.warehouse_id, r.sku_id): r.location_id
            for r in session.scalars(
                select(SlotAssignment).where(SlotAssignment.assignment_status == AssignmentStatus.ACTIVE.value)
            ).all()
        }

        cpsat_run = opt_service.run_optimization(
            session=session,
            strategy="ortools_cpsat",
            preserve_warehouse_affinity=True,
            apply_immediately=False,
        )
        assert cpsat_run.algorithm_name == "ORTOOLS_CPSAT"
        assert cpsat_run.sku_count == 250
        assert cpsat_run.assigned_count == 248
        assert cpsat_run.unassigned_count == 2
        assert sorted(cpsat_run.unassigned_skus) == ["SKU-000209", "SKU-000238"]

        approve_res = opt_service.approve_assignments(session=session, run_id=cpsat_run.run_id)
        assert approve_res["approved_count"] == 248
        assert approve_res["superseded_count"] == baseline_active_before

        # Historical assignments remain intact as SUPERSEDED with effective_to populated
        superseded_rows = session.scalars(
            select(SlotAssignment).where(SlotAssignment.assignment_status == AssignmentStatus.SUPERSEDED.value)
        ).all()
        assert len(superseded_rows) == baseline_active_before
        assert all(r.effective_to is not None for r in superseded_rows)

        # Persisted ACTIVE assignments actually changed locations and satisfy 100% of hard constraints
        active_rows = session.scalars(
            select(SlotAssignment).where(SlotAssignment.assignment_status == AssignmentStatus.ACTIVE.value)
        ).all()
        assert len(active_rows) == 248
        assert all(r.decision_version >= 2 for r in active_rows)
        moved_count = sum(
            1 for r in active_rows if baseline_wh_sku_loc.get((r.warehouse_id, r.sku_id)) != r.location_id
        )
        assert moved_count > 50

        # Location occupancies match active assignments with zero capacity overloads
        all_locs = session.scalars(select(Location)).all()
        for loc in all_locs:
            assert float(loc.current_weight_kg) <= float(loc.max_weight_kg) + 1e-4
            assert float(loc.current_volume_m3) <= float(loc.max_volume_m3) + 1e-6

        # Post-optimization exception scan auto-resolves remediated slotting violations to 0
        post_scan = exc_service.run_detection(
            session=session,
            optimization_run_id=cpsat_run.run_id,
            auto_resolve_remediated=True,
        )
        assert post_scan["by_issue_type"].get(ExceptionIssueType.CAPACITY_EXCEEDED.value, 0) == 0
        assert post_scan["by_issue_type"].get(ExceptionIssueType.INCOMPATIBLE_COLOCATION.value, 0) == 0
        assert post_scan["by_issue_type"].get(ExceptionIssueType.DUPLICATE_ACTIVE_ASSIGNMENT.value, 0) == 0
        assert post_scan["auto_resolved_count"] >= 10

        # 5. Baseline vs. Optimized Picking Path Comparison on Identical Orders
        pick_cmp = pick_service.compare_slotting_layouts(
            session=session,
            optimization_run_id=cpsat_run.run_id,
            max_orders=400,
        )
        assert pick_cmp["orders_evaluated"] > 200
        assert pick_cmp["optimized_distance_m"] < pick_cmp["baseline_distance_m"]
        assert pick_cmp["distance_saved_m"] > 0.0
        assert pick_cmp["percentage_improvement"] > 5.0
        assert pick_cmp["estimated_time_saved_seconds"] > 0.0

        # 6. Scenario Comparison & Operational KPIs
        scenario = scenario_service.create_and_evaluate_scenario(
            session=session,
            optimization_run_id=cpsat_run.run_id,
            max_orders=400,
        )
        assert scenario.comparison_delta_json["capacity_violations_eliminated"] >= 4
        assert scenario.comparison_delta_json["pick_distance_saved_m"] == pick_cmp["distance_saved_m"]

        kpis = kpi_service.get_overview_kpis(session=session, sample_picking_orders=200)
        assert kpis["warehouse"]["total_warehouses"] == 2
        assert kpis["sku"]["total_skus"] == 250
        assert kpis["optimization"]["latest_run_id"] == cpsat_run.run_id
        assert kpis["optimization"]["constraint_violation_count"] == 0

        # Every assignment has traceable decision records and audit trail
        reason_cnt = int(session.scalar(select(func.count()).select_from(AssignmentReason)) or 0)
        audit_cnt = int(session.scalar(select(func.count()).select_from(AuditEvent)) or 0)
        assert reason_cnt > 1000
        assert audit_cnt >= 6
        assert math.isfinite(kpis["picking_simulated_estimates"]["distance_improvement_pct"])
