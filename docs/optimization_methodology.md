# SlotWise AI — Optimization & Simulation Methodology

## 1. Constrained Slotting Problem Formulation

Given a warehouse scope $\mathcal{W}$, active SKUs $\mathcal{I}$, and active unblocked storage slots $\mathcal{J}$, each SKU $i \in \mathcal{I}$ has physical dimensions $(w_i, h_i, d_i)$ in cm, unit weight $m_i$ in kg, unit volume $v_i = \frac{w_i h_i d_i}{10^6}\text{ m}^3$, hazard code $h^{\text{haz}}_i$, temperature requirement $t_i$, and target forward-pick stock quantity $Q_i \ge 1$.

### 1.1 Hard Feasibility Filter ($\mathcal{F} \subseteq \mathcal{I} \times \mathcal{J}$)
Before computing soft scores or invoking a solver, candidate generation evaluates 9 non-negotiable hard constraints for each pair $(i, j)$:
1. Facility & zone status (`Warehouse.status == ACTIVE`, `Zone.status == ACTIVE`).
2. Slot & aisle accessibility (`Location.status == ACTIVE`, `Location.is_blocked == False`, `Aisle.is_blocked == False`).
3. Interior dimensional fit ($w_i \le W_j \land h_i \le H_j \land d_i \le D_j$).
4. Remaining weight capacity ($Q_i m_i \le W_j^{\max} - W_j^{\text{occ}}$).
5. Remaining cubic volume ($Q_i v_i \le V_j^{\max} - V_j^{\text{occ}}$).
6. Thermal regime equality ($t_i = T_{\text{zone}(j)}$).
7. Zone hazard allowance ($h^{\text{haz}}_i \in \text{AllowedHazards}(\text{zone}(j))$ via `zone_hazard_allowances`).
8. Storage dedication policy (`DEDICATED_SINGLE_SKU` vs. `SHARED_COMPATIBLE`).
9. Pairwise co-location compatibility rules (`compatibility_rules` hazard and food/pharma/chemical separation) and heavy-item ergonomic lifting limits ($m_i > 25\text{ kg}$ excluded from non-pallet high racks).

All failed constraints are recorded verbatim in `AssignmentReason.rejection_reasons_json`.

---

## 2. Hybrid Solver Architecture

### 2.1 Exact Constrained Solver (`ORTOOLS_CPSAT`, $|\mathcal{I}| \le 300$)
For single-warehouse, single-zone, SKU-subset, and `SMALL` dataset runs, SlotWise AI formulates a binary integer program in **Google OR-Tools CP-SAT**:
- Binary decision variables $x_{i,j} \in \{0, 1\}$ for top-$K$ feasible candidates $(i, j) \in \mathcal{F}_K$, plus slack variable $u_i \in \{0, 1\}$ indicating an unassignable SKU.
- Objective scales normalized float scores by $10^4$ into exact integer coefficients:
  $$\max \sum_{(i,j) \in \mathcal{F}_K} \lfloor 10000 \cdot S_{\text{total}}(i,j) \rfloor x_{i,j} - 10^7 \sum_{i \in \mathcal{I}} u_i$$
- Enforces single assignment per SKU ($\sum_j x_{i,j} + u_i = 1$), single SKU per dedicated slot ($\sum_i x_{i,j} \le 1$), at most 2 compatible SKUs and a $90\%$ operational capacity headroom cap on `SHARED_COMPATIBLE` slots (preventing mixed-bin picker congestion and $\ge 92\%$ near-exhaustion warnings), and pairwise conflict exclusions ($x_{i,j} + x_{k,j} \le 1$).

### 2.2 Scalable Heuristic Solver (`DETERMINISTIC_GREEDY_LOCAL_SWAP`, $|\mathcal{I}| > 300$)
For `MEDIUM` ($1,000$ SKUs) and `FULL` ($5,000$ SKUs) global runs, avoiding full Cartesian MIP overhead:
1. **Constructive Phase**: Orders SKUs deterministically by `(is_constrained_specialty DESC, velocity_percentile DESC, unit_volume_m3 DESC, sku_id ASC)` and assigns each SKU to its highest-scoring residual-feasible candidate slot while updating residual weight/volume capacities dynamically.
2. **2-Way Local Swap Improvement Phase**: Iteratively evaluates deterministic swaps between pairs of assigned SKUs $(i_1, i_2)$ within the same thermal/hazard regime; accepts any swap $(j_1 \leftrightarrow j_2)$ that satisfies all 9 hard constraints at both destinations and strictly increases $S_{\text{total}}(i_1, j_2) + S_{\text{total}}(i_2, j_1) > S_{\text{total}}(i_1, j_1) + S_{\text{total}}(i_2, j_2) + 10^{-5}$.

---

## 3. Pick-Path Graph Routing & 2-Opt Improvement

- Models warehouse movement from staging dock `(depot_x, depot_y)` through parallel aisle corridors connected by front (`y_start`) and rear (`y_end`) cross-aisles.
- **Blocked-Aisle Awareness**: Any aisle with `is_blocked == True` is removed from valid traversal edges (detouring via nearest unblocked cross-aisle corridor, or flagging historical picks inside blocked aisles as `BLOCKED_AISLE_PICK_PATH` exceptions).
- **Baseline Tour**: Visits order lines in raw order-entry sequence and returns to dock.
- **Optimized Tour**: Constructs a Nearest-Neighbor tour from the dock using aisle-corridor Manhattan distance and refines the sequence via deterministic **2-Opt edge-exchange local search** (never claiming global TSP optimality unless solved to exact optimality).

---

## 4. Replenishment & Labor Workload Simulation

For each active SKU profile with lead time $L_i$ and daily demand standard deviation $\sigma_i$:

$$\text{SafetyStock}_i = Z \cdot \sigma_i \sqrt{L_i}, \qquad \text{ROP}_i = \text{ADV}_i \cdot L_i + \text{SafetyStock}_i$$

When forward-pick inventory $I_i < \text{ROP}_i$, a replenishment request is generated for $Q^{\text{rep}}_i = \lceil \text{TargetStock}_i - I_i \rceil$ with required labor:

$$M^{\text{req}}_i = M_{\text{setup}} + \frac{d(\text{reserve}, \text{pick})}{60 \cdot v_{\text{rep}}} + \frac{Q^{\text{rep}}_i}{100} \cdot M_{\text{handle100}}$$

Aggregate warehouse workload $\sum_{i \in \mathcal{W}} M^{\text{req}}_i$ is compared against `Warehouse.daily_labor_minutes_capacity` to compute labor utilization percentage and flag `REPLENISHMENT_LABOR_OVERLOAD` tasks.
