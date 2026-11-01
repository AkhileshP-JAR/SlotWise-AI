"""Shared Pydantic v2 pagination, error, health, system status, and audit event schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorResponse(BaseModel):
    """Standardized API error payload ensuring internal DB errors are never exposed."""

    error_code: str = Field(..., description="Machine-readable error identifier.")
    message: str = Field(..., description="Human-readable error explanation.")
    details: dict[str, Any] | list[Any] | None = Field(default=None, description="Optional validation context.")


class PaginationMeta(BaseModel):
    """Standard pagination metadata accompanying paginated collection endpoints."""

    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1)
    total_items: int = Field(..., ge=0)
    total_pages: int = Field(..., ge=0)


class PaginatedResponse[T](BaseModel):
    """Generic paginated list response."""

    items: list[T]
    meta: PaginationMeta


class HealthResponse(BaseModel):
    """Lightweight liveness and database connectivity probe response."""

    status: str
    service: str
    version: str
    environment: str
    database_connected: bool
    timestamp: datetime


class SystemStatusResponse(BaseModel):
    """Comprehensive system health, dataset manifest, optimization, ML, and assistant status."""

    status: str
    version: str
    config_version: str
    active_profile: str
    dataset_version: str | None
    seed: int
    database_dialect: str
    database_connected: bool
    write_auth_configured: bool
    table_counts: dict[str, int]
    total_records: int
    last_validation: dict[str, Any] | None
    latest_optimization_run: dict[str, Any] | None
    ml_status: dict[str, Any]
    assistant_status: dict[str, Any]
    timestamp: datetime


class AuditEventOut(BaseModel):
    """Immutable audit log record representation."""

    model_config = ConfigDict(from_attributes=True)

    audit_event_id: str
    event_type: str
    entity_type: str
    entity_id: str
    actor: str
    previous_state_json: dict[str, Any] | None
    new_state_json: dict[str, Any] | None
    metadata_json: dict[str, Any] | None
    created_at: datetime
