"""Operational exception browsing, scanning, and state-machine resolution endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app.config import EnvSettings
from backend.app.dependencies import (
    DomainValidationError,
    EntityNotFoundError,
    get_exception_service,
    get_operations_repo,
    get_pipeline_service,
    get_read_db,
    get_settings,
    get_write_db,
    require_write_access,
)
from backend.app.repositories import OperationsRepository
from backend.app.schemas.common import PaginatedResponse
from backend.app.schemas.operations import (
    ExceptionOut,
    ExceptionResolveRequest,
    ExceptionScanRequest,
    ExceptionScanResponse,
)
from backend.app.services.exception_engine import ExceptionDetectionService
from backend.app.services.pipeline_service import DataPipelineService

router = APIRouter(prefix="/exceptions", tags=["Exception Center"])


@router.get("", response_model=PaginatedResponse[ExceptionOut])
def list_exceptions(
    category: str | None = Query(
        default=None,
        description="Filter by category (BUSINESS_RULE_VIOLATION, OPERATIONAL_WARNING, DATA_QUALITY_VIOLATION, OPTIMIZATION_FAILURE).",
    ),
    issue_type: str | None = Query(
        default=None, description="Filter by issue type (e.g., CAPACITY_EXCEEDED, INCOMPATIBLE_COLOCATION)."
    ),
    severity: str | None = Query(default=None, description="Filter by severity (CRITICAL, HIGH, MEDIUM, LOW)."),
    resolution_status: str | None = Query(
        default=None, description="Filter by lifecycle status (OPEN, IN_REVIEW, RESOLVED, DISMISSED)."
    ),
    entity_type: str | None = Query(
        default=None, description="Filter by affected entity type (LOCATION, SKU, WAREHOUSE, PICK_EVENT)."
    ),
    entity_id: str | None = Query(default=None, description="Filter by affected entity ID."),
    optimization_run_id: str | None = Query(default=None),
    include_ground_truth_eval: bool = Query(
        default=False,
        description="Attach read-only ground_truth_flag strictly for benchmark presentation.",
    ),
    sort_by: Literal["exception_id", "severity", "issue_type", "detected_at", "resolution_status"] = Query(
        default="exception_id"
    ),
    sort_order: Literal["asc", "desc"] = Query(default="asc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=250),
    db: Session = Depends(get_read_db),
    settings: EnvSettings = Depends(get_settings),
    ops_repo: OperationsRepository = Depends(get_operations_repo),
    pipeline: DataPipelineService = Depends(get_pipeline_service),
) -> PaginatedResponse[ExceptionOut]:
    """List operational ExceptionRecords with multi-attribute filtering and pagination."""
    gt_pairs = (
        pipeline.load_ground_truth_pairs_readonly(profile=settings.config_profile)
        if include_ground_truth_eval
        else None
    )
    return ops_repo.list_exceptions(
        session=db,
        category=category,
        issue_type=issue_type,
        severity=severity,
        resolution_status=resolution_status,
        entity_type=entity_type,
        entity_id=entity_id,
        optimization_run_id=optimization_run_id,
        ground_truth_pairs=gt_pairs,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )


@router.post("/scan", response_model=ExceptionScanResponse)
def scan_exceptions(
    payload: ExceptionScanRequest | None = None,
    actor: str = Depends(require_write_access),
    db: Session = Depends(get_write_db),
    exc_service: ExceptionDetectionService = Depends(get_exception_service),
) -> ExceptionScanResponse:
    """Execute an operational exception detection scan against live warehouse tables (zero ground-truth access)."""
    req = payload or ExceptionScanRequest()
    result = exc_service.run_detection(
        session=db,
        optimization_run_id=req.optimization_run_id,
        auto_resolve_remediated=req.auto_resolve_remediated,
        actor=actor,
    )
    return ExceptionScanResponse.model_validate(result)


@router.get("/{exception_id}", response_model=ExceptionOut)
def get_exception(
    exception_id: str,
    include_ground_truth_eval: bool = Query(default=False),
    db: Session = Depends(get_read_db),
    settings: EnvSettings = Depends(get_settings),
    ops_repo: OperationsRepository = Depends(get_operations_repo),
    pipeline: DataPipelineService = Depends(get_pipeline_service),
) -> ExceptionOut:
    """Retrieve a single ExceptionRecord by ID."""
    exc = ops_repo.get_exception(session=db, exception_id=exception_id)
    if exc is None:
        raise EntityNotFoundError("ExceptionRecord", exception_id)

    out = ExceptionOut.model_validate(exc)
    if include_ground_truth_eval:
        gt_pairs = pipeline.load_ground_truth_pairs_readonly(profile=settings.config_profile)
        if gt_pairs is not None:
            out.ground_truth_flag = (exc.issue_type, exc.entity_id) in gt_pairs
    return out


@router.patch("/{exception_id}/resolve", response_model=ExceptionOut)
def resolve_exception(
    exception_id: str,
    payload: ExceptionResolveRequest,
    actor: str = Depends(require_write_access),
    db: Session = Depends(get_write_db),
    exc_service: ExceptionDetectionService = Depends(get_exception_service),
) -> ExceptionOut:
    """Transition an exception's resolution lifecycle status with strict state-machine validation and audit logging."""
    try:
        updated = exc_service.update_exception_status(
            session=db,
            exception_id=exception_id,
            new_status=payload.resolution_status,
            resolution_note=payload.resolution_note,
            actor=actor,
        )
    except KeyError as exc:
        raise EntityNotFoundError("ExceptionRecord", exception_id) from exc
    except ValueError as exc:
        raise DomainValidationError(str(exc)) from exc

    return ExceptionOut.model_validate(updated)
