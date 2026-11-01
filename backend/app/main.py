"""FastAPI application entrypoint, lifespan management, health probe, and global error handlers."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app.api.v1.router import api_v1_router
from backend.app.config import DomainConfig, EnvSettings, get_domain_config
from backend.app.database import init_db
from backend.app.dependencies import (
    ConflictError,
    DomainValidationError,
    EntityNotFoundError,
    get_active_domain_config,
    get_read_db,
    get_settings,
)
from backend.app.schemas.common import ErrorResponse, HealthResponse
from backend.app.services.audit_service import utc_now_naive

logger = logging.getLogger("slotwise.api")

OPENAPI_TAGS: list[dict[str, str]] = [
    {"name": "Health", "description": "Liveness and database connectivity probes."},
    {"name": "System & Audit", "description": "System status, 21-table record counts, and immutable audit trail."},
    {"name": "Warehouses", "description": "Facility master data, zones, hazard allowances, and aisle topology."},
    {"name": "Locations", "description": "Storage slot browsing, capacity utilization, and assignment occupancy."},
    {
        "name": "SKUs & Candidate Slots",
        "description": "SKU master data, demand/ABC/XYZ profiles, and candidate slot evaluations.",
    },
    {
        "name": "Data Pipeline",
        "description": "Deterministic synthetic data generation, 18-rule validation, and SHA-256 manifests.",
    },
    {
        "name": "Slotting Optimization",
        "description": "Constrained OR-Tools CP-SAT / Greedy+Swap optimization and assignment governance.",
    },
    {
        "name": "Picking & Route Optimization",
        "description": "Aisle-network pick tour simulation (Nearest-Neighbor + 2-Opt) and telemetry.",
    },
    {
        "name": "Replenishment & Labor Workload",
        "description": "Reorder-point replenishment simulation and warehouse labor capacity analytics.",
    },
    {
        "name": "Exception Center",
        "description": "Operational, business-rule, and data-quality exception detection and resolution.",
    },
    {
        "name": "Operational Analytics",
        "description": "Executive KPIs, facility utilization, optimization metrics, and F1 benchmark.",
    },
    {
        "name": "Scenario Comparison",
        "description": "Baseline vs. optimized slotting comparison scenarios on identical orders.",
    },
    {
        "name": "Warehouse Decision Assistant",
        "description": "Grounded 6-node LangGraph decision-support assistant.",
    },
]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Ensure relational database tables exist before serving requests."""
    init_db()
    yield


def _error_json(
    status_code: int,
    error_code: str,
    message: str,
    details: dict[str, Any] | list[Any] | None = None,
) -> JSONResponse:
    payload = ErrorResponse(error_code=error_code, message=message, details=details)
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


def create_app() -> FastAPI:
    """Create and configure the versioned SlotWise AI FastAPI application."""
    cfg = get_domain_config()
    application = FastAPI(
        title=f"{cfg.project.name} — Warehouse Slotting & Optimization API",
        description=(
            "Production-inspired REST API for warehouse slotting optimization, explainable multi-factor "
            "candidate scoring, pick-path & replenishment simulation, exception governance, and operational analytics."
        ),
        version=cfg.project.version,
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.exception_handler(EntityNotFoundError)
    async def handle_not_found(_request: Request, exc: EntityNotFoundError) -> JSONResponse:
        return _error_json(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="ENTITY_NOT_FOUND",
            message=exc.message,
            details={"entity_type": exc.entity_type, "entity_id": exc.entity_id},
        )

    @application.exception_handler(DomainValidationError)
    async def handle_domain_validation(_request: Request, exc: DomainValidationError) -> JSONResponse:
        return _error_json(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="DOMAIN_VALIDATION_ERROR",
            message=exc.message,
            details=exc.details,
        )

    @application.exception_handler(ConflictError)
    async def handle_conflict(_request: Request, exc: ConflictError) -> JSONResponse:
        return _error_json(
            status_code=status.HTTP_409_CONFLICT,
            error_code="CONFLICT_ERROR",
            message=exc.message,
            details=exc.details,
        )

    @application.exception_handler(RequestValidationError)
    async def handle_request_validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        sanitized_errors = [
            {
                "loc": [str(part) for part in err.get("loc", ())],
                "msg": str(err.get("msg", "Invalid value")),
                "type": str(err.get("type", "value_error")),
            }
            for err in exc.errors()
        ]
        return _error_json(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            error_code="REQUEST_VALIDATION_ERROR",
            message="Request payload or query parameters failed schema validation.",
            details=sanitized_errors,
        )

    @application.exception_handler(StarletteHTTPException)
    async def handle_http_exception(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error_json(
            status_code=exc.status_code,
            error_code=f"HTTP_{exc.status_code}",
            message=str(exc.detail),
        )

    @application.exception_handler(SQLAlchemyError)
    async def handle_sqlalchemy_error(_request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.exception("Sanitized SQLAlchemy exception during request processing: %s", exc.__class__.__name__)
        return _error_json(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="DATABASE_OPERATION_ERROR",
            message="A database operation failed while processing the request.",
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled server exception: %s", exc.__class__.__name__)
        return _error_json(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="INTERNAL_SERVER_ERROR",
            message="An unexpected internal server error occurred.",
        )

    @application.get("/health", response_model=HealthResponse, tags=["Health"])
    def health_check(
        db: Session = Depends(get_read_db),
        settings: EnvSettings = Depends(get_settings),
        config: DomainConfig = Depends(get_active_domain_config),
    ) -> HealthResponse:
        """Liveness and database connectivity probe."""
        db_ok = False
        try:
            db.execute(text("SELECT 1"))
            db_ok = True
        except SQLAlchemyError:
            db_ok = False

        return HealthResponse(
            status="HEALTHY" if db_ok else "DEGRADED",
            service=config.project.name,
            version=config.project.version,
            environment=settings.env,
            database_connected=db_ok,
            timestamp=utc_now_naive(),
        )

    application.include_router(api_v1_router)
    return application


app = create_app()
