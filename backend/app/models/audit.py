"""ExceptionRecord and AuditEvent traceability models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database import Base


class ExceptionRecord(Base):
    """Detected operational, business-rule, data-quality, or optimization exception.

    Note on Ground-Truth Leakage Prevention (Section 10):
    Operational exception detection populates this table strictly from business rules
    applied to operational tables, without reading ground_truth files. Ground-truth
    matching is performed exclusively in read-only evaluation views/reports.
    """

    __tablename__ = "exceptions"
    __table_args__ = (
        Index("ix_exceptions_issue_severity", "issue_type", "severity"),
        Index("ix_exceptions_entity", "entity_type", "entity_id"),
        Index("ix_exceptions_resolution_status", "resolution_status"),
    )

    exception_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    optimization_run_id: Mapped[str | None] = mapped_column(
        String(24), ForeignKey("optimization_runs.run_id", ondelete="SET NULL"), nullable=True
    )
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    issue_type: Mapped[str] = mapped_column(String(48), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(48), nullable=False)
    related_entity_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    resolution_status: Mapped[str] = mapped_column(String(24), nullable=False, default="OPEN")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_action: Mapped[str] = mapped_column(Text, nullable=False)


class AuditEvent(Base):
    """Immutable audit log capturing every state transition across assignments, runs, and exceptions."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_entity", "entity_type", "entity_id"),
        Index("ix_audit_events_type_created", "event_type", "created_at"),
    )

    audit_event_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(48), nullable=False)
    actor: Mapped[str] = mapped_column(String(48), nullable=False, default="SYSTEM")
    previous_state_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    new_state_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
