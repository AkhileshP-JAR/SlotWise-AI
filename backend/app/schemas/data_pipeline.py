"""Pydantic v2 request and response schemas for synthetic data generation, validation, and manifests."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class DataGenerateRequest(BaseModel):
    """Request parameters for deterministic synthetic dataset generation."""

    profile: Literal["small", "medium", "full"] = Field(default="small", description="Dataset scale profile.")
    seed: int = Field(default=20261022, ge=1, le=2_147_483_647, description="Fixed deterministic RNG seed.")
    inject_anomalies: bool = Field(
        default=True, description="Inject labelled benchmark anomalies and separate ground truth."
    )
    load_into_database: bool = Field(
        default=True, description="Atomically reload operational tables in the active database after generation."
    )


class DataGenerateResponse(BaseModel):
    """Outcome summary of synthetic dataset generation and optional DB reload."""

    status: str
    profile: str
    seed: int
    dataset_version: str
    elapsed_seconds: float
    entity_counts: dict[str, int]
    ground_truth_anomaly_count: int
    ground_truth_breakdown: dict[str, int]
    manifest_path: str
    database_load: dict[str, Any] | None = None


class DataValidateRequest(BaseModel):
    """Request parameters to run the 18-rule DatasetValidator on a generated profile."""

    profile: Literal["small", "medium", "full"] = Field(default="small")


class DataValidateResponse(BaseModel):
    """18-check dataset validation report distinguishing errors, warnings, and expected anomalies."""

    profile: str
    is_valid: bool
    summary: dict[str, Any]
    checks: dict[str, str]
    errors: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    report_path: str


class DataManifestResponse(BaseModel):
    """Reproducibility manifest and SHA-256 file integrity checksums."""

    available: bool
    profile: str
    manifest: dict[str, Any] | None = None
