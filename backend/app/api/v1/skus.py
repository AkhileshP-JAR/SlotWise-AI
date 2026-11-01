"""SKU master-data, analytical SKUProfile, and explainable candidate-slot endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app.config import DomainConfig
from backend.app.dependencies import (
    DomainValidationError,
    EntityNotFoundError,
    get_active_domain_config,
    get_read_db,
    get_sku_repo,
)
from backend.app.models import SKU
from backend.app.optimization.candidates import CandidateGeneratorService
from backend.app.repositories import SKURepository
from backend.app.schemas.common import PaginatedResponse
from backend.app.schemas.sku import (
    EvaluatedCandidateOut,
    SKUCandidatesResponse,
    SKUDetailOut,
    SKUOut,
    SKUProfileOut,
)

router = APIRouter(prefix="/skus", tags=["SKUs & Candidate Slots"])


@router.get("", response_model=PaginatedResponse[SKUOut])
def list_skus(
    search: str | None = Query(default=None, description="Search by SKU ID, SKU name, or category."),
    category: str | None = Query(default=None, description="Filter by product category."),
    hazard_class: str | None = Query(
        default=None, description="Filter by hazard class (NONE, HAZ-FLAM, HAZ-CORR, HAZ-OXID, HAZ-LITH)."
    ),
    temperature_requirement: str | None = Query(
        default=None, description="Filter by thermal requirement (AMBIENT, CHILLED, FROZEN)."
    ),
    demand_velocity: str | None = Query(default=None, description="Filter by velocity tier (FAST, MEDIUM, SLOW)."),
    abc_class: str | None = Query(default=None, description="Filter by Pareto value class (A, B, C)."),
    xyz_class: str | None = Query(default=None, description="Filter by demand variability class (X, Y, Z)."),
    warehouse_id: str | None = Query(default=None, description="Filter SKUs currently assigned to a warehouse."),
    status: str | None = Query(
        default=None, description="Filter by SKU lifecycle status (ACTIVE, DISCONTINUED, HOLD)."
    ),
    sort_by: Literal[
        "sku_id",
        "sku_name",
        "category",
        "unit_price",
        "unit_weight_kg",
        "average_daily_demand",
        "pick_frequency",
        "velocity_percentile",
        "reorder_point",
    ] = Query(default="sku_id"),
    sort_order: Literal["asc", "desc"] = Query(default="asc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=250),
    db: Session = Depends(get_read_db),
    repo: SKURepository = Depends(get_sku_repo),
) -> PaginatedResponse[SKUOut]:
    """List SKUs joined with active demand/ABC/XYZ profiles and active slot placements."""
    return repo.list_skus(
        session=db,
        search=search,
        category=category,
        hazard_class=hazard_class,
        temperature_requirement=temperature_requirement,
        demand_velocity=demand_velocity,
        abc_class=abc_class,
        xyz_class=xyz_class,
        warehouse_id=warehouse_id,
        status=status,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )


@router.get("/{sku_id}", response_model=SKUDetailOut)
def get_sku(
    sku_id: str,
    db: Session = Depends(get_read_db),
    repo: SKURepository = Depends(get_sku_repo),
) -> SKUDetailOut:
    """Retrieve full SKU master attributes, supplier info, analytical profile, daily demand trend, and slot lineage."""
    detail = repo.get_sku_detail(session=db, sku_id=sku_id)
    if detail is None:
        raise EntityNotFoundError("SKU", sku_id)
    return detail


@router.get("/{sku_id}/profile", response_model=SKUProfileOut)
def get_sku_profile(
    sku_id: str,
    db: Session = Depends(get_read_db),
    repo: SKURepository = Depends(get_sku_repo),
) -> SKUProfileOut:
    """Retrieve the active analytical SKUProfile for a SKU."""
    if db.get(SKU, sku_id) is None:
        raise EntityNotFoundError("SKU", sku_id)
    prof = repo.get_active_profile(session=db, sku_id=sku_id)
    if prof is None:
        raise EntityNotFoundError("SKUProfile", sku_id)
    return SKUProfileOut.model_validate(prof)


@router.get("/{sku_id}/candidates", response_model=SKUCandidatesResponse)
def get_sku_candidates(
    sku_id: str,
    warehouse_id: str | None = Query(default=None, description="Optional warehouse filter."),
    zone_id: str | None = Query(default=None, description="Optional zone filter."),
    storage_policy_override: Literal["DEDICATED_SINGLE_SKU", "SHARED_COMPATIBLE"] | None = Query(default=None),
    top_n: int = Query(default=15, ge=1, le=100, description="Number of top feasible candidates to return."),
    max_rejected_samples: int = Query(
        default=15, ge=1, le=100, description="Number of rejected sample slots with explicit rejection reasons."
    ),
    explicit_quantity: int | None = Query(default=None, ge=1, le=100_000),
    vacate_current_slot: bool = Query(
        default=True,
        description="Treat the SKU's own currently occupied slot as available for re-evaluation.",
    ),
    db: Session = Depends(get_read_db),
    config: DomainConfig = Depends(get_active_domain_config),
) -> SKUCandidatesResponse:
    """Evaluate candidate slots for a SKU with 6-factor score breakdowns and explainable hard-constraint rejection reasons."""
    sku_obj = db.get(SKU, sku_id)
    if sku_obj is None:
        raise EntityNotFoundError("SKU", sku_id)

    cand_service = CandidateGeneratorService(
        config=config,
        storage_policy_override=storage_policy_override,
    )
    (
        sku_specs,
        metrics_map,
        loc_specs,
        _,
        compat_service,
        max_wh_dist,
    ) = cand_service.load_operational_context(
        session=db,
        warehouse_id=warehouse_id,
        zone_id=zone_id,
        sku_ids=[sku_id],
        vacate_in_scope_skus=vacate_current_slot,
    )
    if not sku_specs or sku_id not in metrics_map:
        raise DomainValidationError(
            f"SKU '{sku_id}' is inactive or missing an active analytical SKUProfile and cannot be slotted."
        )

    pool = cand_service.evaluate_candidates_for_sku(
        sku=sku_specs[0],
        metrics=metrics_map[sku_id],
        locations=loc_specs,
        compat_service=compat_service,
        max_wh_dist=max_wh_dist,
        top_n=top_n,
        max_rejected_samples=max_rejected_samples,
        explicit_quantity=explicit_quantity,
    )

    return SKUCandidatesResponse(
        sku_id=sku_id,
        warehouse_scope=warehouse_id or "ALL",
        zone_scope=zone_id,
        total_locations_evaluated=pool.total_locations_evaluated,
        total_feasible_count=pool.total_feasible_count,
        total_rejected_count=pool.total_rejected_count,
        rejection_reason_counts=pool.rejection_reason_counts,
        top_feasible=[EvaluatedCandidateOut.model_validate(c.to_dict()) for c in pool.top_feasible],
        rejected_samples=[EvaluatedCandidateOut.model_validate(c.to_dict()) for c in pool.rejected_samples],
    )
