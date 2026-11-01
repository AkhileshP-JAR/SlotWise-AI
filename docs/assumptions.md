# SlotWise AI — Engineering Assumptions & Risk Mitigations

## 1. Key Domain & Modeling Assumptions

1. **Coordinate & Distance System**:
   - Each warehouse models its staging/dispatch dock at `(depot_x=0.0, depot_y=0.0)` meters.
   - Aisles are modeled as parallel vertical corridors connected by front, middle, and rear cross-aisles.
   - Pick travel uses an **aisle-network graph** (Manhattan corridor routing with cross-aisle transitions). When an aisle is marked `is_blocked=True`, graph routing detours through adjacent unblocked aisles; historical/injected baseline pick events traversing a blocked aisle are explicitly flagged as `BLOCKED_AISLE_PICK_PATH` exceptions.
2. **Observation Window & Reference Calendar**:
   - Default synthetic transactions span a deterministic 90-day window (`2026-01-01T00:00:00` to `2026-03-31T23:59:59`) using fixed seed `20261022`.
   - To guarantee byte-identical reproducibility across runs, synthetic entity timestamps (`created_at`, `effective_from`) are anchored deterministically to the reference simulation clock (`2026-04-01T00:00:00`) rather than wall-clock `datetime.now()`, while runtime user actions (new optimization runs, API audit events) record UTC timestamps documented in the reproducibility manifest.
3. **Assigned Inventory Quantity Modeling**:
   - Rather than assuming 1 unit per SKU, target forward-pick quantity per SKU is calculated as $\max(1, \lceil \text{ADV} \times \text{target\_days\_of\_supply} \rceil)$ capped by practical bin replenishment lot sizing, ensuring both unit dimensions and multi-unit weight/volume capacity are validated realistically.
4. **Approval Lifecycle**:
   - Per Section 15 ("Do not automatically approve every recommendation"), optimization runs create assignments in `PROPOSED` status by default (configurable via `auto_approve_assignments`), allowing operators via REST API or Streamlit Page 4 to approve (`PROPOSED -> ACTIVE`, superseding prior `ACTIVE` records to `SUPERSEDED`) or reject (`PROPOSED -> REJECTED`) recommendations with full `AuditEvent` logging.
5. **Ground-Truth Isolation**:
   - `ground_truth.csv` and `ground_truth.json` live strictly in `data/ground_truth/`. No operational table contains `is_anomaly` or `ground_truth_label` columns. Evaluation joins happen exclusively in read-only analytics evaluators and UI audit badges.

---

## 2. Technical Risks & Mitigations

| Risk | Impact | Mitigation Implemented |
|---|---|---|
| Cartesian explosion ($5,000 \text{ SKUs} \times 10,000 \text{ Locations} = 5 \times 10^7$ pairs) in FULL profile | Memory spike & slow optimization | Pre-index locations by `(warehouse_id, temperature_type, hazard_class)` and prune dimensional mismatches vectorized before scoring; retain top-$N$ ($N=15$) feasible candidates per SKU + summary rejection counts. |
| SQLite DDL rejecting intentionally injected baseline anomalies | Cannot test exception detection on loaded DB | Enforce relational FKs and physical positivity (`width > 0`) in DDL, while enforcing occupancy/co-location/duplication rules in the Optimizer, Validator, and Exception Engine. |
| Nondeterministic hash ordering across OS/Python runs | Reproducibility hash mismatch | Sort all generated records by primary key, normalize CSV line endings (`\n`), use isolated `numpy.random.Generator(PCG64(seed))`, and anchor simulation timestamps. |
| Missing external LLM API keys in local evaluation | Assistant endpoint failure | LangGraph state machine executes SQL retrieval and deterministic analysis first; when `SLOTWISE_LLM_PROVIDER=none`, a structured deterministic synthesis node formats cited answers with zero external calls. |
