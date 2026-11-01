# SlotWise AI — Known Limitations & Scope Boundaries

In accordance with engineering transparency standards, the following scope boundaries and limitations apply:

1. **Synthetic vs. Physical Telemetry**:
   - All warehouse topologies, SKU catalogs, and 90-day order histories are deterministically synthesized (`seed=20261022`). Simulated travel-distance and pick-duration improvements represent modeled engineering estimates based on Manhattan aisle-corridor geometry and constant associate walk speeds, not live WMS RF-scanner telemetry.
2. **3D Bin-Packing Approximation**:
   - Physical slot fit checks orthogonal exterior dimensions ($w_i \le W_j, h_i \le H_j, d_i \le D_j$) and aggregate cubic volume ($Q_i v_i \le V_j^{\text{avail}}$). Full NP-hard 3D guillotine/rotational carton nesting inside irregular bins is approximated via target utilization headroom ($\le 92\%$ operational threshold).
3. **Heuristic vs. Global Optimality at Scale**:
   - Scopes up to 300 SKUs use exact constrained integer programming (OR-Tools CP-SAT) over top-$K$ feasible candidates per SKU. Larger multi-warehouse runs (`MEDIUM`/`FULL`) use a deterministic greedy + 2-way local-swap heuristic and 2-opt tour improvement, which guarantee constraint feasibility and monotonic score improvement without claiming global MIP/TSP optimality.
4. **Intermittent Daily Demand Nature**:
   - In fulfillment centers with hundreds or thousands of SKUs, slow-moving items exhibit intermittent zero-demand days. Point daily forecasts have high variance; multi-step cumulative horizon forecasts (`horizon_wape_pct`) provide the primary reliable signal for reorder point sizing and slot buffer allocations.
5. **Single-Facility Primary Affinity**:
   - SKUs are assigned to storage locations within their primary designated fulfillment warehouse to prevent operational fragmentation. Multi-facility dynamic rebalancing and cross-docking transfers across distinct geographic warehouses are outside the current optimization scope.
6. **Deterministic Labor Availability**:
   - Replenishment labor availability is modeled per facility per shift based on scheduled associate headcounts, standard walk speeds, and average pick/replenish unit rates, without dynamic real-time modeling of worker fatigue, breaks, or unscheduled absenteeism.
7. **Advisory Decision Support Only**:
   - Neither the optimization engine nor the optional LangGraph Warehouse Decision Assistant autonomously executes physical inventory moves; optimization outputs default to `PROPOSED` lifecycle status for explicit human review and approval.
