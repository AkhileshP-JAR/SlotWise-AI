"""Batched transactional database loader for SlotWise AI operational datasets."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import Engine, delete, func, insert, select
from sqlalchemy.orm import sessionmaker

from backend.app.config import DATA_DIR
from backend.app.database import get_engine, init_db, session_scope
from backend.app.models import (
    SKU,
    Aisle,
    AuditEvent,
    CompatibilityRule,
    ExceptionRecord,
    HazardClass,
    Location,
    OptimizationRun,
    Order,
    OrderLine,
    Picker,
    PickEvent,
    Replenishment,
    Scenario,
    SKUProfile,
    SlotAssignment,
    Supplier,
    Warehouse,
    Zone,
    ZoneHazardAllowance,
)
from backend.app.models.assignment import AssignmentReason
from backend.app.services.data_generator import OPERATIONAL_TABLE_ORDER
from backend.app.utils.ids import format_audit_event_id

TABLE_MODEL_MAP: dict[str, Any] = {
    "warehouses": Warehouse,
    "hazard_classes": HazardClass,
    "zones": Zone,
    "zone_hazard_allowances": ZoneHazardAllowance,
    "compatibility_rules": CompatibilityRule,
    "aisles": Aisle,
    "locations": Location,
    "suppliers": Supplier,
    "skus": SKU,
    "sku_profiles": SKUProfile,
    "orders": Order,
    "order_lines": OrderLine,
    "pickers": Picker,
    "pick_events": PickEvent,
    "replenishments": Replenishment,
    "slot_assignments": SlotAssignment,
}

DATETIME_COLUMNS: dict[str, set[str]] = {
    "warehouses": {"created_at"},
    "sku_profiles": {"effective_from", "effective_to"},
    "orders": {"order_date", "created_at"},
    "pick_events": {"pick_timestamp"},
    "replenishments": {"created_at", "completed_at"},
    "slot_assignments": {"effective_from", "effective_to", "created_at"},
}

BOOLEAN_COLUMNS: dict[str, set[str]] = {
    "hazard_classes": {"requires_segregation"},
    "compatibility_rules": {"is_hard_constraint"},
    "aisles": {"is_blocked"},
    "locations": {"is_blocked"},
    "skus": {"is_fragile"},
}


def _clean_batch_records(table_name: str, chunk_df: pd.DataFrame) -> list[dict[str, Any]]:
    """Normalize NaN values to None and parse ISO datetime and boolean columns for SQLAlchemy Core insert."""
    dt_cols = DATETIME_COLUMNS.get(table_name, set())
    bool_cols = BOOLEAN_COLUMNS.get(table_name, set())

    cleaned_df = chunk_df.astype(object).where(pd.notna(chunk_df), None)
    records: list[dict[str, Any]] = cleaned_df.to_dict(orient="records")

    for rec in records:
        for dcol in dt_cols:
            val = rec.get(dcol)
            if val is not None and isinstance(val, str):
                rec[dcol] = datetime.fromisoformat(val)
        for bcol in bool_cols:
            val = rec.get(bcol)
            if val is not None:
                if isinstance(val, str):
                    rec[bcol] = val.strip().lower() in {"true", "1", "yes"}
                else:
                    rec[bcol] = bool(val)
    return records


class DatabaseLoader:
    """Streams generated CSVs into SQLite or PostgreSQL using batched SQLAlchemy transactions."""

    def __init__(self, engine: Engine | None = None, batch_size: int = 2500) -> None:
        self.engine = engine or get_engine()
        self.batch_size = max(100, batch_size)
        self.session_factory = sessionmaker(
            bind=self.engine, autoflush=False, autocommit=False, expire_on_commit=False
        )

    def reset_operational_tables(self) -> None:
        """Ensure schema exists and clear existing records in reverse dependency order."""
        init_db(engine=self.engine, drop_existing=False)
        deletion_order = [
            AuditEvent,
            ExceptionRecord,
            Scenario,
            AssignmentReason,
            SlotAssignment,
            OptimizationRun,
            Replenishment,
            PickEvent,
            Picker,
            OrderLine,
            Order,
            SKUProfile,
            SKU,
            Supplier,
            Location,
            Aisle,
            CompatibilityRule,
            ZoneHazardAllowance,
            Zone,
            HazardClass,
            Warehouse,
        ]
        with session_scope(self.session_factory) as session:
            for model_cls in deletion_order:
                session.execute(delete(model_cls))

    def load_profile_from_disk(
        self,
        profile: str,
        data_root: Path = DATA_DIR,
        reset_existing: bool = True,
    ) -> dict[str, Any]:
        """Stream CSV chunks in topological FK order into the configured database."""
        gen_dir = data_root / "generated" / profile
        if not gen_dir.exists():
            raise FileNotFoundError(f"Generated dataset directory not found: {gen_dir}")

        t0 = time.perf_counter()
        if reset_existing:
            self.reset_operational_tables()
        else:
            init_db(engine=self.engine, drop_existing=False)

        inserted_counts: dict[str, int] = {}

        with session_scope(self.session_factory) as session:
            for table_name in OPERATIONAL_TABLE_ORDER:
                csv_path = gen_dir / f"{table_name}.csv"
                model_cls = TABLE_MODEL_MAP[table_name]
                total_rows = 0

                for chunk in pd.read_csv(csv_path, chunksize=self.batch_size, keep_default_na=True):
                    if chunk.empty:
                        continue
                    batch_records = _clean_batch_records(table_name, chunk)
                    session.execute(insert(model_cls), batch_records)
                    total_rows += len(batch_records)

                inserted_counts[table_name] = total_rows

            # Record immutable audit event for dataset load
            existing_audits = session.scalar(select(func.count()).select_from(AuditEvent)) or 0
            session.add(
                AuditEvent(
                    audit_event_id=format_audit_event_id(2026, int(existing_audits) + 1),
                    event_type="DATASET_LOADED",
                    entity_type="DATASET",
                    entity_id=f"slotwise-{profile}",
                    actor="DATA_LOADER",
                    previous_state_json=None,
                    new_state_json={"profile": profile, "inserted_counts": inserted_counts},
                    metadata_json={"batch_size": self.batch_size},
                    created_at=datetime(2026, 4, 1, 0, 0, 0),
                )
            )

        elapsed_s = round(time.perf_counter() - t0, 3)
        total_records = sum(inserted_counts.values())
        return {
            "profile": profile,
            "total_records_inserted": total_records,
            "elapsed_seconds": elapsed_s,
            "throughput_rows_per_sec": round(total_records / max(elapsed_s, 1e-4), 1),
            "inserted_counts": inserted_counts,
        }

    def verify_database_counts(self) -> dict[str, int]:
        """Query actual persisted table counts from the database."""
        counts: dict[str, int] = {}
        with session_scope(self.session_factory) as session:
            for table_name in OPERATIONAL_TABLE_ORDER:
                model_cls = TABLE_MODEL_MAP[table_name]
                cnt = session.scalar(select(func.count()).select_from(model_cls)) or 0
                counts[table_name] = int(cnt)
        return counts
