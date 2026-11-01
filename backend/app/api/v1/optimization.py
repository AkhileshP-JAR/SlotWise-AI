"""Constrained slotting optimization execution, governance approval/rejection, and assignment lineage endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app.config import EnvSettings
from backend.app.dependencies import (
    DomainValidationError,
    EntityNotFoundError,
    get_exception_service,
    get_optimization_repo,
    get_optimizer_service,
    get_pipeline_service,
    get_read_db,
    get_settings,
    get_warehouse_repo,
    get_write_db,
    require_write_access,
)
from backend.app.models import Zone
from backend.app.optimization.solver import SlottingOptimizerService
from backend.app.repositories import OptimizationRepository, WarehouseRepository
from backend.app.schemas.common import PaginatedResponse
from backend.app.schemas.optimization import (
    AssignmentApprovalRequest,
    AssignmentApprovalResponse,
    AssignmentRejectionRequest,
    AssignmentRejectionResponse,
    OptimizationRunOut,
    OptimizationRunRequest,
    SlotAssignmentOut,
)
from backend.app.services.exception_engine import ExceptionDetectionService
from backend.app.services.pipeline_service import DataPipelineService

router = APIRouter(prefix="/optimization", tags=["Slotting Optimization"])


@router.post("/run", response_model=OptimizationRunOut)
def run_optimization(
    payload: OptimizationRunRequest,
    actor: str = Depends(require_write_access),
    db: Session = Depends(get_write_db),
    settings: EnvSettings = Depends(get_settings),
    optimizer: SlottingOptimizerService = Depends(get_optimizer_service),
    opt_repo: OptimizationRepository = Depends(get_optimization_repo),
    wh_repo: WarehouseRepository = Depends(get_warehouse_repo),
    exc_service: ExceptionDetectionService = Depends(get_exception_service),
    pipeline: DataPipelineService = Depends(get_pipeline_service),
) -> OptimizationRunOut:
    """Execute constrained slotting optimization (OR-Tools CP-SAT or Greedy+Swap) and persist versioned decisions."""
    if payload.warehouse_id and wh_repo.get_warehouse_detail(db, payload.warehouse_id) is None:
        raise EntityNotFoundError("Warehouse", payload.warehouse_id)
    if payload.zone_id and db.get(Zone, payload.zone_id) is None:
        raise EntityNotFoundError("Zone", payload.zone_id)

    manifest_info = pipeline.get_manifest(profile=settings.config_profile)
    ds_version = (manifest_info.get("manifest") or {}).get("dataset_version")

    summary = optimizer.run_optimization(
        session=db,
        warehouse_id=payload.warehouse_id,
        zone_id=payload.zone_id,
        sku_ids=payload.sku_ids,
        strategy=payload.strategy,
        storage_policy_override=payload.storage_policy_override,
        preserve_warehouse_affinity=payload.preserve_warehouse_affinity,
        apply_immediately=payload.apply_immediately,
        use_ml_forecasts=payload.use_ml_forecasts,
        dataset_version=ds_version,
        actor=actor,
    )

    if payload.apply_immediately:
        exc_service.run_detection(
            session=db,
            optimization_run_id=summary.run_id,
            auto_resolve_remediated=True,
            actor=actor,
        )

    persisted_run = opt_repo.get_run(db, summary.run_id)
    if persisted_run is None:
        raise EntityNotFoundError("OptimizationRun", summary.run_id)
    return OptimizationRunOut.model_validate(persisted_run)


@router.get("/runs", response_model=PaginatedResponse[OptimizationRunOut])
def list_optimization_runs(
    warehouse_scope: str | None = Query(
        default=None, description="Filter by warehouse scope (e.g., ALL, WH-001)."
    ),
    algorithm_name: str | None = Query(
        default=None, description="Filter by algorithm (ORTOOLS_CPSAT, DETERMINISTIC_GREEDY_LOCAL_SWAP)."
    ),
    run_status: str | None = Query(default=None, description="Filter by run status (COMPLETED, PARTIAL, FAILED)."),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=250),
    db: Session = Depends(get_read_db),
    repo: OptimizationRepository = Depends(get_optimization_repo),
) -> PaginatedResponse[OptimizationRunOut]:
    """List immutable OptimizationRun execution records ordered newest first."""
    return repo.list_runs(
        session=db,
        warehouse_scope=warehouse_scope,
        algorithm_name=algorithm_name,
        run_status=run_status,
        page=page,
        page_size=page_size,
    )


@router.get("/runs/{run_id}", response_model=OptimizationRunOut)
def get_optimization_run(
    run_id: str,
    db: Session = Depends(get_read_db),
    repo: OptimizationRepository = Depends(get_optimization_repo),
) -> OptimizationRunOut:
    """Retrieve metadata, solver parameters, and KPI results for a single optimization run."""
    run = repo.get_run(session=db, run_id=run_id)
    if run is None:
        raise EntityNotFoundError("OptimizationRun", run_id)
    return OptimizationRunOut.model_validate(run)


@router.get("/runs/{run_id}/assignments", response_model=PaginatedResponse[SlotAssignmentOut])
def list_run_assignments(
    run_id: str,
    warehouse_id: str | None = Query(default=None),
    sku_id: str | None = Query(default=None),
    assignment_status: str | None = Query(
        default=None, description="Filter by status (PROPOSED, ACTIVE, SUPERSEDED, REJECTED)."
    ),
    include_reasons: bool = Query(
        default=False,
        description="Attach persisted AssignmentReason candidate evaluations and rejection rationales.",
    ),
    sort_by: Literal[
        "slot_assignment_id",
        "sku_id",
        "location_id",
        "assignment_score",
        "decision_version",
        "created_at",
    ] = Query(default="assignment_score"),
    sort_order: Literal["asc", "desc"] = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_read_db),
    repo: OptimizationRepository = Depends(get_optimization_repo),
) -> PaginatedResponse[SlotAssignmentOut]:
    """List versioned SlotAssignment decisions generated by a specific optimization run."""
    if repo.get_run(session=db, run_id=run_id) is None:
        raise EntityNotFoundError("OptimizationRun", run_id)
    return repo.list_assignments(
        session=db,
        run_id=run_id,
        warehouse_id=warehouse_id,
        sku_id=sku_id,
        assignment_status=assignment_status,
        include_reasons=include_reasons,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )


@router.post("/runs/{run_id}/approve", response_model=AssignmentApprovalResponse)
def approve_run_assignments(
    run_id: str,
    payload: AssignmentApprovalRequest | None = None,
    actor: str = Depends(require_write_access),
    db: Session = Depends(get_write_db),
    optimizer: SlottingOptimizerService = Depends(get_optimizer_service),
    repo: OptimizationRepository = Depends(get_optimization_repo),
    exc_service: ExceptionDetectionService = Depends(get_exception_service),
) -> AssignmentApprovalResponse:
    """Approve PROPOSED slot assignments of a run to ACTIVE, superseding prior ACTIVE history non-destructively."""
    if repo.get_run(session=db, run_id=run_id) is None:
        raise EntityNotFoundError("OptimizationRun", run_id)

    req = payload or AssignmentApprovalRequest()
    try:
        outcome = optimizer.approve_assignments(
            session=db,
            run_id=run_id,
            assignment_ids=req.assignment_ids,
            actor=actor,
        )
    except ValueError as exc:
        raise DomainValidationError(str(exc)) from exc

    rescan_summary = None
    if req.auto_resolve_exceptions and outcome["approved_count"] > 0:
        rescan_summary = exc_service.run_detection(
            session=db,
            optimization_run_id=run_id,
            auto_resolve_remediated=True,
            actor=actor,
        )

    return AssignmentApprovalResponse(
        run_id=run_id,
        approved_count=outcome["approved_count"],
        superseded_count=outcome["superseded_count"],
        reconciled_locations=outcome["reconciled_locations"],
        exception_rescan=rescan_summary,
    )


@router.post("/runs/{run_id}/reject", response_model=AssignmentRejectionResponse)
def reject_run_assignments(
    run_id: str,
    payload: AssignmentRejectionRequest | None = None,
    actor: str = Depends(require_write_access),
    db: Session = Depends(get_write_db),
    optimizer: SlottingOptimizerService = Depends(get_optimizer_service),
    repo: OptimizationRepository = Depends(get_optimization_repo),
) -> AssignmentRejectionResponse:
    """Reject PROPOSED slot assignments while keeping current ACTIVE placements untouched."""
    if repo.get_run(session=db, run_id=run_id) is None:
        raise EntityNotFoundError("OptimizationRun", run_id)

    req = payload or AssignmentRejectionRequest()
    try:
        outcome = optimizer.reject_assignments(
            session=db,
            run_id=run_id,
            assignment_ids=req.assignment_ids,
            reason=req.reason,
            actor=actor,
        )
    except ValueError as exc:
        raise DomainValidationError(str(exc)) from exc

    return AssignmentRejectionResponse.model_validate(outcome)


@router.get("/assignments/history", response_model=PaginatedResponse[SlotAssignmentOut])
def list_assignment_history(
    warehouse_id: str | None = Query(default=None),
    sku_id: str | None = Query(default=None),
    location_id: str | None = Query(default=None),
    assignment_status: str | None = Query(
        default=None, description="Filter by status (PROPOSED, ACTIVE, SUPERSEDED, REJECTED)."
    ),
    include_reasons: bool = Query(default=False),
    sort_by: Literal[
        "slot_assignment_id",
        "sku_id",
        "location_id",
        "assignment_score",
        "decision_version",
        "created_at",
    ] = Query(default="decision_version"),
    sort_order: Literal["asc", "desc"] = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_read_db),
    repo: OptimizationRepository = Depends(get_optimization_repo),
) -> PaginatedResponse[SlotAssignmentOut]:
    """Query full non-destructive assignment lineage across ACTIVE, SUPERSEDED, PROPOSED, and REJECTED records."""
    return repo.list_assignments(
        session=db,
        run_id=None,
        warehouse_id=warehouse_id,
        sku_id=sku_id,
        location_id=location_id,
        assignment_status=assignment_status,
        include_reasons=include_reasons,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )
