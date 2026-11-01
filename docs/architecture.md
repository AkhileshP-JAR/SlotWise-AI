# SlotWise AI — System Architecture

## 1. Architectural Overview

**SlotWise AI** is a modular, deterministic, production-inspired warehouse slotting optimization and operational analytics platform. Every component—synthetic data pipelines, relational storage, demand analytics, candidate generation, constrained optimization, pick-path routing, replenishment simulation, exception detection, ML demand forecasting, LangGraph decision support, FastAPI REST endpoints, and the 9-page Streamlit dashboard—operates over a **single shared relational domain model**.

### Design Principles

1. **Zero Paid / External Dependencies for Core Execution**: Runs locally on Python 3.12 + SQLite (with WAL & strict Foreign Keys enabled) and supports drop-in PostgreSQL switching via `SLOTWISE_DATABASE_URL`.
2. **Hard Constraints Before Soft Scores**: Multi-factor scoring ranks feasible slots only; physical dimensions, weight/volume capacity, hazard allowances, temperature compatibility, co-location rules, and unblocked status are non-negotiable hard constraints.
3. **Immutable Historical Traceability**: Optimization runs (`optimization_runs`), versioned slot assignments (`slot_assignments`), evaluated candidate rationales (`assignment_reasons`), and state changes (`audit_events`) are append-only / lifecycle-versioned. Historical assignments are never overwritten.
4. **Strict Ground-Truth Isolation**: Injected anomalies are recorded in `data/ground_truth/` exclusively for precision/recall/F1 evaluation. Operational tables, exception detectors, and ML feature pipelines never read ground-truth columns.

---

## 2. System Component Architecture

```mermaid
flowchart TD
    subgraph ConfigLayer["Configuration & Reproducibility"]
        YAML["YAML Profiles (default / small / medium / full)"]
        ENV[".env / Pydantic EnvSettings"]
        SEED["Fixed Seed (20261022) & Manifest SHA-256"]
    end

    subgraph DataPipeline["Synthetic Data & Validation Pipeline"]
        GEN["Deterministic Data Generator"]
        ANOM["Labelled Anomaly Injector"]
        GT["Isolated Ground Truth (CSV / JSON)"]
        VAL["18-Rule Relational & Business Validator"]
        LOAD["Batch Database Loader"]
    end

    subgraph Storage["Persistence Layer (SQLAlchemy 2.x + Alembic)"]
        DB[("SQLite (Default) / PostgreSQL (Optional)\n21 Normalized Domain Tables")]
    end

    subgraph CoreEngine["Core Domain & Optimization Services"]
        VEL["Demand Velocity & ABC/XYZ Engine"]
        CAND["Candidate Slot & Constraint Evaluator"]
        SCORE["Normalized 6-Factor Scorer"]
        OPT["Hybrid Optimizer (OR-Tools CP-SAT + Greedy/Swap)"]
        PICK["Aisle-Graph & 2-Opt Pick Path Simulator"]
        REP["Replenishment & Labor Workload Simulator"]
        EXC["Operational & Rule Exception Detector"]
        KPI["KPI & Precision/Recall Evaluator"]
    end

    subgraph OptionalIntelligence["Optional ML & Agentic AI"]
        ML["Chronological SKU Forecaster + MLflow"]
        AGENT["LangGraph Grounded Decision Assistant\n(LLM Adapter + Deterministic Fallback)"]
    end

    subgraph Interfaces["Delivery Layer"]
        API["FastAPI REST API (/api/v1/*)"]
        UI["9-Page Streamlit + Plotly Enterprise Dashboard"]
    end

    ConfigLayer --> DataPipeline
    GEN --> ANOM
    ANOM --> GT
    ANOM --> VAL
    VAL --> LOAD
    LOAD --> DB
    DB <--> CoreEngine
    GT -. "Read-only F1 Evaluation Only" .-> KPI
    DB <--> OptionalIntelligence
    CoreEngine --> API
    OptionalIntelligence --> API
    API <--> UI
```

---

## 3. Entity-Relationship Diagram

```mermaid
erDiagram
    WAREHOUSES ||--o{ ZONES : "contains (1:N)"
    WAREHOUSES ||--o{ AISLES : "contains (1:N)"
    WAREHOUSES ||--o{ ORDERS : "fulfills (1:N)"
    WAREHOUSES ||--o{ PICKERS : "employs (1:N)"
    WAREHOUSES ||--o{ REPLENISHMENTS : "executes (1:N)"
    WAREHOUSES ||--o{ SLOT_ASSIGNMENTS : "hosts (1:N)"

    ZONES ||--o{ ZONE_HAZARD_ALLOWANCES : "permits (1:N)"
    HAZARD_CLASSES ||--o{ ZONE_HAZARD_ALLOWANCES : "allowed_in (1:N)"
    ZONES ||--o{ AISLES : "organizes (1:N)"
    ZONES ||--o{ LOCATIONS : "partitions (1:N)"
    AISLES ||--o{ LOCATIONS : "serves (1:N)"

    SUPPLIERS ||--o{ SKUS : "supplies (1:N)"
    HAZARD_CLASSES ||--o{ SKUS : "classifies (1:N)"
    SKUS ||--o{ SKU_PROFILES : "profiled_by (1:N)"
    SKUS ||--o{ ORDER_LINES : "ordered_in (1:N)"
    SKUS ||--o{ SLOT_ASSIGNMENTS : "assigned_via (1:N)"
    SKUS ||--o{ REPLENISHMENTS : "replenished_by (1:N)"

    ORDERS ||--|{ ORDER_LINES : "comprises (1:N)"
    ORDERS ||--o{ PICK_EVENTS : "generates (1:N)"
    ORDER_LINES ||--o{ PICK_EVENTS : "picked_as (1:N)"
    LOCATIONS ||--o{ PICK_EVENTS : "picked_from (1:N)"
    PICKERS ||--o{ PICK_EVENTS : "executes (1:N)"

    LOCATIONS ||--o{ SLOT_ASSIGNMENTS : "receives (1:N)"
    OPTIMIZATION_RUNS ||--o{ SLOT_ASSIGNMENTS : "produces (1:N)"
    OPTIMIZATION_RUNS ||--o{ ASSIGNMENT_REASONS : "explains (1:N)"
    OPTIMIZATION_RUNS ||--o{ SCENARIOS : "evaluated_in (1:N)"
    OPTIMIZATION_RUNS ||--o{ EXCEPTIONS : "flags (1:N)"
```

---

## 4. Data Generation & Ground-Truth Separation Pipeline

```mermaid
flowchart LR
    CFG["Profile YAML + Seed 20261022"] --> M["1. Master Data\n(Warehouses, Zones, Aisles, Locations, Suppliers, SKUs)"]
    M --> TX["2. Realistic Transactions\n(90-Day Seasonality, Day-of-Week, Pareto Demand)"]
    TX --> PROF["3. Derived Profiles\n(ADV, StdDev, Velocity, ABC/XYZ, ROP, Baseline Slots)"]
    PROF --> PICKREP["4. Operational Traces\n(Pick Events, Replenishment Queue)"]
    PICKREP --> INJ["5. Controlled Anomaly Injector"]
    INJ --> OP["Operational Exports\n(CSV / JSON / SQLite Seed)"]
    INJ --> GT["Separate Ground Truth\n(ground_truth.csv / .json)"]
    OP --> MAN["SHA-256 Reproducibility Manifest"]
```

---

## 5. Slotting Optimization Workflow

```mermaid
flowchart TD
    START["Optimization Request\n(Scope: All / Warehouse / Zone / SKU Subset)"] --> LOAD["Load Active SKUs, Profiles, Zones, Locations & Rules"]
    LOAD --> CAND["Generate Candidate Matrix\nCheck 9 Hard Physical & Safety Constraints"]
    CAND --> REJ["Persist Explicit Rejection Reasons\nfor Infeasible Candidates"]
    CAND --> SCORE["Compute 6 Normalized Score Components\nfor Feasible Candidates"]
    SCORE --> SIZE{"Feasible Scope <= 300 SKUs?"}
    SIZE -- "Yes" --> CPSAT["OR-Tools CP-SAT Constrained Integer Solver\n(Maximizes Total Weighted Score s.t. Weight/Volume/Policy)"]
    SIZE -- "No" --> GREEDY["Deterministic Velocity-Ordered Greedy\n+ 2-Way Local Swap Improvement"]
    CPSAT --> PERSIST["Create Immutable OptimizationRun\n+ Versioned PROPOSED/ACTIVE SlotAssignments\n+ AssignmentReasons + AuditEvents"]
    GREEDY --> PERSIST
    PERSIST --> UNASSN["Flag Unassigned SKUs as\nOPTIMIZATION_FAILURE Exceptions"]
```

---

## 6. Exception Lifecycle State Machine

```mermaid
stateDiagram-v2
    [*] --> OPEN : Detected by Exception Engine
    OPEN --> IN_REVIEW : Triaged by Operator / API
    OPEN --> RESOLVED : Direct Remediation Applied
    OPEN --> DISMISSED : Acknowledged Operational Trade-off
    IN_REVIEW --> RESOLVED : Fix Verified
    IN_REVIEW --> DISMISSED : False Positive / Accepted Risk
    IN_REVIEW --> OPEN : Re-opened
    RESOLVED --> IN_REVIEW : Regression / Audit Re-check
    DISMISSED --> IN_REVIEW : Audit Re-check
```

---

## 7. LangGraph Warehouse Decision Assistant Workflow

```mermaid
flowchart TD
    Q["User Natural Language Query"] --> INTENT["Node 1: Deterministic Intent & Entity Extractor"]
    INTENT --> CTX["Node 2: SQL Warehouse & SKU Context Retriever"]
    CTX --> KPI["Node 3: KPI, Candidate Rationale & Exception Retriever"]
    KPI --> DET["Node 4: Deterministic Analytical Computation"]
    DET --> SYNTH{"Optional LLM Configured?"}
    SYNTH -- "No (Default Local)" --> FALLBACK["Node 5a: Grounded Structured Explanation Builder"]
    SYNTH -- "Yes (Groq/OpenAI)" --> LLM["Node 5b: Constrained Pydantic LLM Synthesizer"]
    FALLBACK --> GUARD["Node 6: Fact & ID Grounding Validator"]
    LLM --> GUARD
    GUARD --> ANS["Structured AssistantResponse\n(Answer, Citations, Execution Trace, Confidence)"]
```
