"""Data pipeline orchestration service wrapping SyntheticDataGenerator, DatasetValidator, and DatabaseLoader."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from backend.app.config import DATA_DIR, load_domain_config
from backend.app.database import get_engine
from backend.app.services.data_generator import SyntheticDataGenerator
from backend.app.services.data_loader import DatabaseLoader
from backend.app.validators.dataset_validator import DatasetValidator


class DataPipelineService:
    """Thin application service coordinating dataset generation, 18-rule validation, manifests, and DB loading."""

    def __init__(self, data_root: Path = DATA_DIR, engine: Engine | None = None) -> None:
        self.data_root = data_root
        self.engine = engine

    def generate_dataset(
        self,
        profile: str = "small",
        seed: int = 20261022,
        inject_anomalies: bool = True,
        load_into_database: bool = True,
    ) -> dict[str, Any]:
        """Generate deterministic synthetic dataset files and optionally reload the operational database."""
        cfg = load_domain_config(profile=profile)
        cfg.dataset.anomaly_injection.enabled = bool(inject_anomalies)

        t0 = time.perf_counter()
        generator = SyntheticDataGenerator(config=cfg, profile=profile, seed=seed)
        bundle = generator.generate()
        manifest = generator.export_bundle(bundle, output_root=self.data_root)
        elapsed = round(time.perf_counter() - t0, 3)

        db_load_summary: dict[str, Any] | None = None
        if load_into_database:
            loader = DatabaseLoader(engine=self.engine or get_engine())
            db_load_summary = loader.load_profile_from_disk(
                profile=profile, data_root=self.data_root, reset_existing=True
            )

        manifest_path = (self.data_root / "manifests" / f"{bundle.profile}_manifest.json").as_posix()
        return {
            "status": "SUCCESS",
            "profile": bundle.profile,
            "seed": bundle.seed,
            "dataset_version": manifest.get("dataset_version", f"slotwise-{bundle.profile}-seed{bundle.seed}"),
            "elapsed_seconds": elapsed,
            "entity_counts": manifest["entity_counts"],
            "ground_truth_anomaly_count": manifest["ground_truth_anomaly_count"],
            "ground_truth_breakdown": manifest["ground_truth_breakdown"],
            "manifest_path": manifest_path,
            "database_load": db_load_summary,
        }

    def validate_dataset(self, profile: str = "small") -> dict[str, Any]:
        """Execute the 18-rule DatasetValidator against a generated profile on disk."""
        gen_dir = self.data_root / "generated" / profile
        if not gen_dir.exists():
            raise FileNotFoundError(
                f"Generated dataset for profile '{profile}' does not exist at {gen_dir.as_posix()}."
            )

        cfg = load_domain_config(profile=profile)
        validator = DatasetValidator(config=cfg, profile=profile)
        tables, gt_df = validator.load_tables_from_disk(profile=profile, data_root=self.data_root)
        report = validator.validate(tables=tables, ground_truth_df=gt_df)
        report_path = validator.save_report(report, output_root=self.data_root)

        return {
            "profile": report["profile"],
            "is_valid": bool(report["is_valid"]),
            "summary": report["summary"],
            "checks": report["checks"],
            "errors": report["errors"],
            "warnings": report["warnings"],
            "report_path": report_path.as_posix(),
        }

    def get_manifest(self, profile: str = "small") -> dict[str, Any]:
        """Read the SHA-256 reproducibility manifest for a profile if present."""
        manifest_file = self.data_root / "manifests" / f"{profile}_manifest.json"
        if not manifest_file.exists():
            return {"available": False, "profile": profile, "manifest": None}
        payload = json.loads(manifest_file.read_text(encoding="utf-8"))
        return {"available": True, "profile": profile, "manifest": payload}

    def get_last_validation_summary(self, profile: str = "small") -> dict[str, Any] | None:
        """Read the latest persisted validation report summary if available."""
        report_file = self.data_root / "manifests" / f"{profile}_validation_report.json"
        if not report_file.exists():
            return None
        payload = json.loads(report_file.read_text(encoding="utf-8"))
        return {
            "profile": payload.get("profile", profile),
            "is_valid": payload.get("is_valid"),
            "summary": payload.get("summary"),
        }

    def load_ground_truth_pairs_readonly(self, profile: str = "small") -> set[tuple[str, str]] | None:
        """Read-only lookup of (expected_issue_type, entity_id) strictly for UI/API presentation badges."""
        gt_path = self.data_root / "ground_truth" / profile / "ground_truth.json"
        if not gt_path.exists():
            return None
        payload = json.loads(gt_path.read_text(encoding="utf-8"))
        return {(str(a["expected_issue_type"]), str(a["entity_id"])) for a in payload.get("anomalies", [])}
