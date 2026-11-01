# SlotWise AI — Data Dictionary & Schema Reference

All tables are managed via **SQLAlchemy 2.x** and **Alembic** migrations (`0001_initial_schema`). Foreign keys are strictly enforced in both PostgreSQL and SQLite (`PRAGMA foreign_keys=ON`).

---

## 1. Deterministic ID Conventions

| Entity | Format Pattern | Regex | Example |
|---|---|---|---|
| Warehouse | `WH-<3d>` | `^WH-\d{3}$` | `WH-001` |
| Zone | `Z<3d>` | `^Z\d{3}$` | `Z001` |
| Aisle | `AISLE-<zone_id>-<2d>` | `^AISLE-Z\d{3}-\d{2}$` | `AISLE-Z001-01` |
| Location | `LOC-<zone_id>-<6d>` | `^LOC-Z\d{3}-\d{6}$` | `LOC-Z001-000001` |
| Supplier | `SUP-<4d>` | `^SUP-\d{4}$` | `SUP-0001` |
| SKU | `SKU-<6d>` | `^SKU-\d{6}$` | `SKU-000001` |
| SKU Profile | `PRF-<sku_id>-<2d>` | `^PRF-SKU-\d{6}-\d{2}$` | `PRF-SKU-000001-01` |
| Order | `ORD-<year>-<6d>` | `^ORD-\d{4}-\d{6}$` | `ORD-2026-000001` |
| Order Line | `OL-<order_id>-<3d>` | `^OL-ORD-\d{4}-\d{6}-\d{3}$` | `OL-ORD-2026-000001-001` |
| Picker | `PKR-WH<3d>-<3d>` | `^PKR-WH\d{3}-\d{3}$` | `PKR-WH001-001` |
| Pick Event | `PICK-<order_id>-<3d>` | `^PICK-ORD-\d{4}-\d{6}-\d{3}$` | `PICK-ORD-2026-000001-001` |
| Slot Assignment | `SLOT-<year>-<6d>` | `^SLOT-\d{4}-\d{6}$` | `SLOT-2026-000001` |
| Replenishment | `REP-<year>-<6d>` | `^REP-\d{4}-\d{6}$` | `REP-2026-000001` |
| Optimization Run | `OPT-<year>-<6d>` | `^OPT-\d{4}-\d{6}$` | `OPT-2026-000001` |
| Exception | `EXC-<year>-<6d>` | `^EXC-\d{4}-\d{6}$` | `EXC-2026-000001` |
| Audit Event | `AUD-<year>-<6d>` | `^AUD-\d{4}-\d{6}$` | `AUD-2026-000001` |
| Scenario | `SCN-<year>-<5d>` | `^SCN-\d{4}-\d{5}$` | `SCN-2026-00001` |
| Compatibility Rule | `RULE-<3d>` | `^RULE-\d{3}$` | `RULE-001` |

---

## 2. Entity Specifications & Relationships

### 2.1 `warehouses`
Physical distribution centers. **Cardinality**: `1 Warehouse : N Zones`, `1 Warehouse : N Aisles`, `1 Warehouse : N Orders`, `1 Warehouse : N Pickers`.

| Column | Type | Key / Constraint | Description |
|---|---|---|---|
| `warehouse_id` | `VARCHAR(16)` | PK | Deterministic ID (`WH-001`) |
| `warehouse_name` | `VARCHAR(120)` | NOT NULL | Facility display name |
| `warehouse_type` | `VARCHAR(40)` | NOT NULL | `REGIONAL_DC`, `FULFILLMENT_CENTER`, `COLD_STORAGE_HUB`, `HAZMAT_FACILITY` |
| `city`, `state`, `country` | `VARCHAR(80)` | NOT NULL | Geographic location |
| `total_area_sqft` | `FLOAT` | `> 0` | Facility floor area in sq. ft. |
| `operating_hours` | `VARCHAR(40)` | NOT NULL | Schedule string (e.g., `06:00-22:00`, `24/7`) |
| `daily_labor_minutes_capacity` | `FLOAT` | `> 0` | Daily available replenishment labor budget (minutes) |
| `depot_x`, `depot_y` | `FLOAT` | NOT NULL | Staging/dispatch dock coordinates (meters) |
| `status` | `VARCHAR(24)` | NOT NULL | `ACTIVE`, `MAINTENANCE`, `INACTIVE` |
| `created_at` | `DATETIME` | NOT NULL | Record creation timestamp |

### 2.2 `hazard_classes` & `zone_hazard_allowances` (M:N Bridge)
Standardized hazard classes and many-to-many bridge table mapping zones to permitted hazard classes.

- `hazard_classes`: `hazard_class_id` (PK: `NONE`, `FLAMMABLE`, `CORROSIVE`, `OXIDIZER`, `TOXIC`, `LITHIUM_BATTERY`), `class_name`, `requires_segregation` (BOOL), `severity_level` (INT), `description`.
- `zone_hazard_allowances`: Composite PK (`zone_id` FK -> `zones.zone_id`, `hazard_class_id` FK -> `hazard_classes.hazard_class_id`).

### 2.3 `zones`
Environmental and operational subdivisions inside a warehouse. **Cardinality**: `N Zones : 1 Warehouse`, `1 Zone : N Locations`.

| Column | Type | Key / Constraint | Description |
|---|---|---|---|
| `zone_id` | `VARCHAR(16)` | PK | Deterministic ID (`Z001`) |
| `warehouse_id` | `VARCHAR(16)` | FK -> `warehouses` | Owning warehouse |
| `zone_name` | `VARCHAR(100)` | NOT NULL | Human-readable zone label |
| `zone_type` | `VARCHAR(40)` | NOT NULL | `FAST_PICK`, `GENERAL`, `BULK_RESERVE`, `COLD_CHAIN`, `HAZMAT`, `OVERSIZE` |
| `temperature_type` | `VARCHAR(32)` | NOT NULL | `AMBIENT`, `CONTROLLED_ROOM`, `CHILLED`, `FROZEN` |
| `allowed_hazard_classes` | `VARCHAR(255)` | NOT NULL | Comma-delimited sorted hazard codes synchronized with bridge table |
| `max_weight_kg` | `FLOAT` | `> 0` | Aggregate zone weight limit |
| `max_volume_m3` | `FLOAT` | `> 0` | Aggregate zone volume capacity |
| `status` | `VARCHAR(24)` | NOT NULL | `ACTIVE`, `RESTRICTED`, `INACTIVE` |

### 2.4 `compatibility_rules`
Explicit pairwise co-location prohibitions (`rule_id` PK, `rule_type`, `primary_attribute`, `conflicting_attribute`, `scope` [`SAME_LOCATION`, `SAME_RACK`, `SAME_ZONE`], `severity`, `is_hard_constraint`, `description`).

### 2.5 `aisles`
Corridor geometry for pick-path graph routing (`aisle_id` PK, `zone_id` FK, `warehouse_id` FK, `aisle_number`, `x_start`, `y_start`, `x_end`, `y_end`, `width_m`, `is_blocked`, `congestion_factor >= 1.0`, `status`).

### 2.6 `locations`
Discrete addressable storage slots. **Cardinality**: `N Locations : 1 Zone`, `N Locations : 1 Aisle`, `1 Location : N SlotAssignments (historical)`.

| Column | Type | Key / Constraint | Description |
|---|---|---|---|
| `location_id` | `VARCHAR(24)` | PK | Deterministic ID (`LOC-Z001-000001`) |
| `zone_id` | `VARCHAR(16)` | FK -> `zones` | Parent zone |
| `aisle_id` | `VARCHAR(24)` | FK -> `aisles` | Serving aisle corridor |
| `rack_id`, `bin_id` | `VARCHAR(24)` | NOT NULL | Physical rack and bin identifiers |
| `shelf_level` | `INTEGER` | NOT NULL | Vertical level (`1` = floor .. `5` = high rack) |
| `x_coordinate`, `y_coordinate` | `FLOAT` | NOT NULL | Position in meters relative to warehouse dock |
| `width_cm`, `height_cm`, `depth_cm` | `FLOAT` | `> 0` | Interior slot dimensions in cm |
| `max_weight_kg`, `max_volume_m3` | `FLOAT` | `> 0` | Physical capacity limits |
| `current_weight_kg`, `current_volume_m3` | `FLOAT` | `>= 0` | Currently occupied weight & volume |
| `accessibility_type` | `VARCHAR(32)` | NOT NULL | `GOLDEN_ZONE`, `STANDARD_REACH`, `FLOOR_PALLET`, `HIGH_RACK_FORKLIFT` |
| `storage_policy` | `VARCHAR(32)` | NOT NULL | `DEDICATED_SINGLE_SKU` or `SHARED_COMPATIBLE` |
| `is_blocked` | `BOOLEAN` | NOT NULL | Physical access obstruction flag |
| `status` | `VARCHAR(24)` | NOT NULL | `ACTIVE`, `BLOCKED`, `MAINTENANCE`, `INACTIVE` |

### 2.7 `suppliers` & `skus`
- `suppliers`: `supplier_id` (PK), `supplier_name`, `country`, `average_lead_time_days > 0`, `reliability_score`, `status`.
- `skus`: `sku_id` (PK), `sku_name`, `category`, `unit_price > 0`, `unit_weight_kg > 0`, `width_cm > 0`, `height_cm > 0`, `depth_cm > 0`, `is_fragile`, `hazard_class` (FK -> `hazard_classes`), `temperature_requirement`, `storage_type`, `supplier_id` (nullable FK -> `suppliers`), `status`.

### 2.8 `sku_profiles`
Derived analytical profile per SKU (`1 SKU : N versioned SKUProfiles`, 1 active profile where `effective_to IS NULL`).

| Column | Type | Description |
|---|---|---|
| `profile_id` | `VARCHAR(32)` PK | `PRF-SKU-000001-01` |
| `sku_id` | `VARCHAR(16)` FK | Referenced SKU |
| `average_daily_demand` | `FLOAT` | `total_units_sold / observation_days` |
| `demand_std_dev` | `FLOAT` | Sample standard deviation of daily unit demand |
| `demand_velocity` | `VARCHAR(16)` | `FAST`, `MEDIUM`, or `SLOW` (demand-frequency mover class) |
| `velocity_percentile` | `FLOAT` | Normalized empirical percentile in `[0.0, 1.0]` |
| `pick_frequency` | `FLOAT` | Order lines per observation day (`total_order_lines / observation_days`) |
| `total_units_sold`, `total_order_lines` | `INTEGER` | Exact aggregated source transaction totals |
| `abc_class` | `VARCHAR(4)` | `A`, `B`, or `C` by cumulative revenue (`quantity * unit_price`) |
| `xyz_class` | `VARCHAR(4)` | `X`, `Y`, or `Z` by demand coefficient of variation (`std_dev / ADV`) |
| `turnover_ratio` | `FLOAT` | Annualized demand divided by average inventory |
| `safety_stock` | `FLOAT` | `Z * demand_std_dev * sqrt(lead_time_days)` |
| `reorder_point` | `FLOAT` | `ADV * lead_time_days + safety_stock` |
| `target_days_of_supply` | `INTEGER` | Target forward-pick stock coverage days |
| `current_on_hand_qty` | `INTEGER` | Snapshot forward-pick inventory units |
| `replenishment_frequency` | `FLOAT` | Expected replenishment cycles per 30 days |
| `peak_demand_multiplier` | `FLOAT` | Ratio of peak-7-day daily demand to overall ADV |
| `effective_from`, `effective_to` | `DATETIME` | Temporal validity window |

### 2.9 `orders`, `order_lines`, `pickers`, `pick_events`
- `orders`: `order_id` (PK), `warehouse_id` (FK), `order_date`, `order_priority`, `order_status`, `customer_region`, `created_at`.
- `order_lines`: `order_line_id` (PK), `order_id` (FK), `sku_id` (FK), `quantity > 0`, `unit_price > 0`, `line_value` (`round(quantity * unit_price, 2)`).
- `pickers`: `picker_id` (PK), `warehouse_id` (FK), `picker_name`, `shift`, `walk_speed_mps`, `pick_rate_per_hour`, `status`.
- `pick_events`: `pick_event_id` (PK), `order_id` (FK), `order_line_id` (FK), `sku_id` (FK), `location_id` (FK), `quantity_picked`, `picker_id` (FK), `pick_timestamp`, `pick_duration_seconds`, `travel_distance_m`, `aisle_sequence`, `event_status`.

### 2.10 `replenishments`
Replenishment workload records (`replenishment_id` PK, `warehouse_id` FK, `sku_id` FK, `source_location_id` nullable FK, `destination_location_id` FK, `required_quantity`, `replenishment_quantity`, `priority`, `labor_minutes_required`, `assigned_labor_minutes`, `replenishment_status`, `created_at`, `completed_at`).

### 2.11 `optimization_runs`, `slot_assignments`, `assignment_reasons`, `scenarios`
- `optimization_runs`: Immutable run header (`run_id` PK, scope, dataset/config/algorithm versions, weights JSON, seed, candidate/assignment/rejection/exception counts, KPI summary JSON, `execution_duration_ms`, `run_status`, `created_at`).
- `slot_assignments`: Versioned slotting records (`slot_assignment_id` PK, `optimization_run_id` nullable FK, `sku_id` FK, `location_id` FK, `warehouse_id` FK, `assignment_status` [`PROPOSED`, `ACTIVE`, `SUPERSEDED`, `REJECTED`], `assigned_quantity`, `allocated_weight_kg`, `allocated_volume_m3`, total + 6 component scores, `effective_from`, `effective_to`, `created_at`, `decision_version`, `decision_reason`).
- `assignment_reasons`: Candidate-level audit trail (`reason_id` PK, `optimization_run_id` FK, `slot_assignment_id` nullable FK, `sku_id` FK, `candidate_location_id` FK, `is_selected`, `is_feasible`, `rank_position`, `total_score`, `score_breakdown_json`, `hard_constraints_checked_json`, `rejection_reasons_json`, `created_at`).
- `scenarios`: Baseline vs. optimized comparison snapshots (`scenario_id` PK, `scenario_name`, `description`, `warehouse_id`, `optimization_run_id` FK, `parameters_json`, `baseline_metrics_json`, `optimized_metrics_json`, `comparison_delta_json`, `created_at`).

### 2.12 `exceptions` & `audit_events`
- `exceptions`: Operational, data-quality, business-rule, and optimization failures (`exception_id` PK, `optimization_run_id` nullable FK, `category`, `issue_type`, `severity`, `entity_type`, `entity_id`, `related_entity_ids_json`, `description`, `detected_at`, `resolution_status`, `resolved_at`, `resolution_note`, `recommended_action`).
- `audit_events`: Immutable state-transition log (`audit_event_id` PK, `event_type`, `entity_type`, `entity_id`, `actor`, `previous_state_json`, `new_state_json`, `metadata_json`, `created_at`).
