"""Synthetic data generation, 18-rule dataset validation, and SHA-256 manifest endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query

from backend.app.config import EnvSettings
from backend.app.dependencies import (
    EntityNotFoundError,
    get_pipeline_service,
    get_settings,
    require_write_access,
)
from backend.app.schemas.data_pipeline import (
    DataGenerateRequest,
    DataGenerateResponse,
    DataManifestResponse,
    DataValidateRequest,
    DataValidateResponse,
)
from backend.app.services.pipeline_service import DataPipelineService

router = APIRouter(prefix="/data", tags=["Data Pipeline"])


@router.post("/generate", response_model=DataGenerateResponse)
def generate_data(
    payload: DataGenerateRequest,
    _actor: str = Depends(require_write_access),
    pipeline: DataPipelineService = Depends(get_pipeline_service),
) -> DataGenerateResponse:
    """Generate deterministic synthetic warehouse data and optionally reload the operational database."""
    result = pipeline.generate_dataset(
        profile=payload.profile,
        seed=payload.seed,
        inject_anomalies=payload.inject_anomalies,
        load_into_database=payload.load_into_database,
    )
    return DataGenerateResponse.model_validate(result)


@router.post("/validate", response_model=DataValidateResponse)
def validate_data(
    payload: DataValidateRequest,
    _actor: str = Depends(require_write_access),
    pipeline: DataPipelineService = Depends(get_pipeline_service),
) -> DataValidateResponse:
    """Run the 18-check DatasetValidator against a generated dataset profile."""
    try:
        result = pipeline.validate_dataset(profile=payload.profile)
    except FileNotFoundError as exc:
        raise EntityNotFoundError("GeneratedDatasetProfile", payload.profile) from exc
    return DataValidateResponse.model_validate(result)


@router.get("/manifest", response_model=DataManifestResponse)
def get_data_manifest(
    profile: Literal["small", "medium", "full"] | None = Query(
        default=None, description="Dataset profile; defaults to active runtime profile."
    ),
    settings: EnvSettings = Depends(get_settings),
    pipeline: DataPipelineService = Depends(get_pipeline_service),
) -> DataManifestResponse:
    """Retrieve the reproducibility manifest and SHA-256 checksums for a generated profile."""
    target_profile = profile or settings.config_profile
    result = pipeline.get_manifest(profile=target_profile)
    return DataManifestResponse.model_validate(result)
