"""Version 1 API router aggregation for SlotWise AI."""

from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.v1 import (
    analytics,
    assistant,
    data_pipeline,
    exceptions,
    locations,
    ml,
    optimization,
    picking,
    replenishment,
    scenarios,
    skus,
    system,
    warehouses,
)

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(system.router)
api_v1_router.include_router(warehouses.router)
api_v1_router.include_router(locations.router)
api_v1_router.include_router(skus.router)
api_v1_router.include_router(data_pipeline.router)
api_v1_router.include_router(optimization.router)
api_v1_router.include_router(picking.router)
api_v1_router.include_router(replenishment.router)
api_v1_router.include_router(exceptions.router)
api_v1_router.include_router(analytics.router)
api_v1_router.include_router(scenarios.router)
api_v1_router.include_router(assistant.router)
api_v1_router.include_router(ml.router)
