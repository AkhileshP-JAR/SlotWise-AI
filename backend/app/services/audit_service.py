"""Immutable AuditEvent logging service for full state-change traceability (Section 16)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.models.audit import AuditEvent
from backend.app.utils.ids import extract_trailing_sequence, format_audit_event_id


def utc_now_naive() -> datetime:
    """Return current UTC datetime stored as naive UTC timestamp for cross-DB SQLite/Postgres consistency."""
    return datetime.now(UTC).replace(tzinfo=None)


class AuditService:
    """Appends immutable AuditEvent records within the caller's database transaction."""

    def __init__(self, reference_year: int = 2026) -> None:
        self.reference_year = reference_year

    def _next_seq(self, session: Session) -> int:
        max_id = session.scalar(select(func.max(AuditEvent.audit_event_id)))
        return extract_trailing_sequence(max_id) + 1

    def log_event(
        self,
        session: Session,
        event_type: str,
        entity_type: str,
        entity_id: str,
        actor: str = "SYSTEM",
        previous_state: dict[str, Any] | None = None,
        new_state: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
        seq_override: int | None = None,
    ) -> AuditEvent:
        """Create and stage an immutable AuditEvent record."""
        seq = seq_override if seq_override is not None else self._next_seq(session)
        event = AuditEvent(
            audit_event_id=format_audit_event_id(self.reference_year, seq),
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            actor=actor,
            previous_state_json=previous_state,
            new_state_json=new_state,
            metadata_json=metadata,
            created_at=timestamp or utc_now_naive(),
        )
        session.add(event)
        return event
