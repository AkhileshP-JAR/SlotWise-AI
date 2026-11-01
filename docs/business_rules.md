# SlotWise AI — Business Rules & Validation Specifications

## 1. Demand Velocity vs. ABC / XYZ Classification

SlotWise AI strictly separates **physical movement velocity** (which drives pick-travel optimization) from **monetary ABC classification** (which drives inventory investment control) and **XYZ variability** (which drives safety-stock buffering).

### 1.1 Demand Velocity & Mover Class
Given an observation horizon of $T$ days ($T = 90$ by default):

$$\text{ADV}_i = \frac{\sum_{t=1}^{T} q_{i,t}}{T}, \qquad f_i = \frac{L_i}{T}$$

where $q_{i,t}$ is units demanded of SKU $i$ on day $t$, and $L_i$ is total order lines containing SKU $i$.
We define a composite movement index $M_i = 0.5 \cdot \text{rank\_pct}(\text{ADV}_i) + 0.5 \cdot \text{rank\_pct}(f_i)$:
- **FAST**: $M_i \ge 0.80$ (top 20% physical movers)
- **MEDIUM**: $0.35 \le M_i < 0.80$ (middle 45%)
- **SLOW**: $M_i < 0.35$ (bottom 35%)

### 1.2 ABC Classification (Cumulative Revenue Value)
Let $V_i = \sum (\text{line\_value})$ be cumulative revenue of SKU $i$. Sorting SKUs in descending order of $V_i$ (tie-broken deterministically by `sku_id`):
- **Class A**: Cumulative value share $\le 0.80$
- **Class B**: Cumulative value share $\in (0.80, 0.95]$
- **Class C**: Cumulative value share $> 0.95$ (or zero-revenue SKUs)

### 1.3 XYZ Classification (Demand Variability)
Using Coefficient of Variation $\text{CV}_i = \frac{\sigma_i}{\text{ADV}_i}$ (for $\text{ADV}_i > 0$, else `Z`):
- **Class X**: $\text{CV}_i \le 0.50$ (stable, predictable demand)
- **Class Y**: $0.50 < \text{CV}_i \le 1.00$ (moderate seasonality/fluctuation)
- **Class Z**: $\text{CV}_i > 1.00$ or $\text{ADV}_i = 0$ (erratic/lumpy demand)

---

## 2. Hard Physical, Environmental & Co-Location Constraints

A candidate pair $(\text{SKU } i, \text{Location } j)$ in Zone $z$ and Warehouse $w$ with target assigned quantity $Q_{i,j} \ge 1$ is **feasible** if and only if all 9 hard checks pass:

1. **Warehouse & Zone Active**: `w.status == "ACTIVE"` and `z.status == "ACTIVE"`.
2. **Location Available & Unblocked**: `j.status == "ACTIVE"`, `j.is_blocked == False`, and `aisle.is_blocked == False`.
3. **Dimensional Fit**:
   $$w_i \le W_j \quad \land \quad h_i \le H_j \quad \land \quad d_i \le D_j$$
4. **Unit & Aggregate Weight Capacity**:
   $$Q_{i,j} \cdot \text{weight}_i \le (\text{max\_weight}_j - \text{occupied\_weight}_j)$$
5. **Unit & Aggregate Volume Capacity**:
   $$\text{vol}_i = \frac{w_i \cdot h_i \cdot d_i}{10^6}, \qquad Q_{i,j} \cdot \text{vol}_i \le (\text{max\_volume}_j - \text{occupied\_volume}_j)$$
6. **Temperature Exact Match**: `i.temperature_requirement == z.temperature_type`.
7. **Hazard Allowance**: `i.hazard_class` must exist in `zone_hazard_allowances` for zone $z$.
8. **Storage Policy & Dedication**: Under `DEDICATED_SINGLE_SKU`, location $j$ cannot hold another active SKU. Under `SHARED_COMPATIBLE`, co-located SKUs must satisfy all `compatibility_rules`.
9. **Handling / Fragility Rule**: Heavy SKUs ($\text{weight}_i > 25\text{ kg}$) cannot be placed on `HIGH_RACK_FORKLIFT` unless `storage_type == "PALLET"`; fragile SKUs cannot co-locate with `INDUSTRIAL_HARDWARE` in shared bins.

Every failed check appends an explicit machine-readable code (e.g., `DIM_WIDTH_EXCEEDED`, `TEMP_MISMATCH`, `HAZARD_NOT_PERMITTED`, `WEIGHT_CAPACITY_INSUFFICIENT`, `LOCATION_BLOCKED`) to `rejection_reasons_json`.

---

## 3. Multi-Factor Normalized Scoring Methodology

For feasible pairs $(i, j)$, each of the 6 components is normalized to $[0, 1]$:

1. **Demand Velocity Score ($S_{\text{vel}}$)**: Alignment between SKU velocity percentile $p_i \in [0, 1]$ and prime ergonomic/forward-pick accessibility $a_j \in [0, 1]$ (`GOLDEN_ZONE=1.0`, `STANDARD_REACH=0.8`, `FLOOR_PALLET=0.65`, `HIGH_RACK_FORKLIFT=0.35`):
   $$S_{\text{vel}}(i,j) = 1 - |p_i - a_j|$$
2. **Pick Frequency Score ($S_{\text{freq}}$)**: Alignment between normalized pick frequency $\hat{f}_i \in [0, 1]$ and proximity to dispatch dock $\hat{d}_j = 1 - \frac{\text{dist}(j, \text{dock})}{D_{\max}}$:
   $$S_{\text{freq}}(i,j) = 1 - |\hat{f}_i - \hat{d}_j|$$
3. **Distance Score ($S_{\text{dist}}$)**: Direct dock-proximity efficiency weighted by pick intensity:
   $$S_{\text{dist}}(i,j) = (0.3 + 0.7 \hat{f}_i)\cdot \hat{d}_j + (1 - (0.3 + 0.7 \hat{f}_i))\cdot (1 - \hat{d}_j)$$
   *(Ensures fast/frequent pick SKUs strongly prefer near-dock slots while very slow movers preserve near-dock slots for fast movers!)*
4. **Capacity Utilization Score ($S_{\text{cap}}$)**: Penalizes both wasteful under-utilization of large bays and tight >92% over-stuffing relative to target $\tau = 0.78$:
   $$u_{i,j} = \max\left(\frac{\text{post\_vol}_j}{\text{max\_vol}_j}, \frac{\text{post\_wt}_j}{\text{max\_wt}_j}\right), \qquad S_{\text{cap}}(i,j) = \max\left(0, 1 - \frac{|u_{i,j} - \tau|}{\max(\tau, 1 - \tau)}\right)$$
5. **Replenishment Efficiency Score ($S_{\text{rep}}$)**: Rewards placing high-replenishment SKUs in floor/accessible reserve-adjacent aisles with sufficient cubic capacity to hold target days-of-supply without excessive micro-replenishments.
6. **Zone Compatibility Score ($S_{\text{zone}}$)**: Rewards exact functional zone affinity (e.g., `FAST` mover in `FAST_PICK` = `1.0`, `HAZMAT` in dedicated `HAZMAT` zone = `1.0`, `OVERSIZE_FLOOR` in `OVERSIZE` = `1.0`, generic fallback zone = `0.70`).

**Total Weighted Score**:
$$S_{\text{total}}(i,j) = 0.30 S_{\text{vel}} + 0.20 S_{\text{freq}} + 0.15 S_{\text{dist}} + 0.15 S_{\text{cap}} + 0.10 S_{\text{rep}} + 0.10 S_{\text{zone}}$$

---

## 4. Data Validation Rules (18 Checks)

The validation framework (`backend/app/validators/`) executes 18 deterministic checks classifying findings into `ERROR`, `WARNING`, or `EXPECTED_LABELLED_ANOMALY`:

1. `PK_UNIQUENESS`: Primary key uniqueness across all tables.
2. `FK_INTEGRITY`: Referential integrity across all parent-child foreign keys.
3. `ID_CONVENTION_FORMAT`: Regex compliance with Section 6 deterministic ID rules.
4. `ENUM_DOMAIN_VALIDITY`: All status, priority, hazard, temperature, ABC/XYZ values belong to valid enumerations.
5. `REQUIRED_NON_NULL`: Mandatory operational columns contain no nulls.
6. `DIMENSION_POSITIVITY`: All SKU and Location `width_cm`, `height_cm`, `depth_cm > 0`.
7. `WEIGHT_POSITIVITY`: All `unit_weight_kg`, `max_weight_kg > 0` and `current_weight_kg >= 0`.
8. `VOLUME_CONSISTENCY`: Location `max_volume_m3` reconciles with `(width_cm * height_cm * depth_cm) / 1e6`.
9. `CAPACITY_CONSTRAINTS`: Flags locations where `current_weight_kg > max_weight_kg` or `current_volume_m3 > max_volume_m3`.
10. `TEMPORAL_DATE_ORDERING`: `effective_from <= effective_to`, `order_date <= pick_timestamp`, `created_at <= completed_at`.
11. `LIFECYCLE_TRANSITIONS`: Validates state transitions against `VALID_ASSIGNMENT_TRANSITIONS` and `VALID_EXCEPTION_TRANSITIONS`.
12. `DUPLICATE_ACTIVE_ASSIGNMENTS`: Detects multiple active assignments on `DEDICATED_SINGLE_SKU` locations.
13. `WAREHOUSE_ZONE_CONSISTENCY`: `SlotAssignment.warehouse_id == Location.zone.warehouse_id`.
14. `SKU_PROFILE_CONSISTENCY`: Recomputes `ADV` and `ABC` from `order_lines` to verify zero drift.
15. `ORDER_LINE_ARITHMETIC`: Verifies `abs(line_value - round(quantity * unit_price, 2)) <= 0.01`.
16. `PICK_EVENT_RECONCILIATION`: Verifies picked quantities and blocked-aisle traversal consistency.
17. `REPLENISHMENT_RECONCILIATION`: Verifies labor minutes calculation and warehouse daily labor capacity bounds.
18. `GROUND_TRUTH_SEPARATION`: Verifies zero ground-truth columns exist in operational tables or ML feature matrices.
