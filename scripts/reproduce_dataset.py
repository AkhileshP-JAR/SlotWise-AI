"""Dual-run determinism and SHA-256 reproducibility verifier (Section 18)."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import ARTIFACTS_DIR, load_domain_config  # noqa: E402
from backend.app.services.data_generator import SyntheticDataGenerator  # noqa: E402
from backend.app.validators.dataset_validator import DatasetValidator  # noqa: E402


def verify_reproducibility(profile: str = "small", seed: int = 20261022) -> dict:
    """Generate the same dataset profile twice in isolated directories and verify byte-identical outputs."""
    cfg = load_domain_config(profile)
    with tempfile.TemporaryDirectory(prefix=f"slotwise_repro_{profile}_") as tmp_base:
        dir_a = Path(tmp_base) / "run_a"
        dir_b = Path(tmp_base) / "run_b"

        gen_a = SyntheticDataGenerator(config=cfg, profile=profile, seed=seed)
        bundle_a = gen_a.generate()
        man_a = gen_a.export_bundle(bundle_a, output_root=dir_a, export_sample=False)

        gen_b = SyntheticDataGenerator(config=cfg, profile=profile, seed=seed)
        bundle_b = gen_b.generate()
        man_b = gen_b.export_bundle(bundle_b, output_root=dir_b, export_sample=False)

        validator = DatasetValidator(config=cfg, profile=profile)
        val_a = validator.validate(*validator.load_tables_from_disk(profile=profile, data_root=dir_a))
        val_b = validator.validate(*validator.load_tables_from_disk(profile=profile, data_root=dir_b))

    counts_match = man_a["entity_counts"] == man_b["entity_counts"]
    hashes_a = man_a["file_checksums_sha256"]
    hashes_b = man_b["file_checksums_sha256"]
    mismatched_hashes = {
        rel_path: {"run_a": hashes_a.get(rel_path), "run_b": hashes_b.get(rel_path)}
        for rel_path in sorted(set(hashes_a) | set(hashes_b))
        if hashes_a.get(rel_path) != hashes_b.get(rel_path)
    }
    hashes_match = len(mismatched_hashes) == 0
    gt_counts_match = (
        man_a["ground_truth_anomaly_count"] == man_b["ground_truth_anomaly_count"]
        and man_a["ground_truth_breakdown"] == man_b["ground_truth_breakdown"]
    )
    validation_match = (
        val_a["is_valid"]
        and val_b["is_valid"]
        and val_a["summary"] == val_b["summary"]
        and val_a["checks"] == val_b["checks"]
    )

    is_reproducible = bool(counts_match and hashes_match and gt_counts_match and validation_match)
    report = {
        "profile": profile,
        "seed": seed,
        "is_reproducible": is_reproducible,
        "entity_counts_match": counts_match,
        "file_sha256_hashes_match": hashes_match,
        "ground_truth_counts_match": gt_counts_match,
        "validation_outcomes_match": validation_match,
        "total_files_hashed": len(hashes_a),
        "entity_counts": man_a["entity_counts"],
        "ground_truth_anomaly_count": man_a["ground_truth_anomaly_count"],
        "mismatched_hashes": mismatched_hashes,
        "sample_checksums_sha256": dict(list(hashes_a.items())[:6]),
    }

    reports_dir = ARTIFACTS_DIR / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_file = reports_dir / f"{profile}_reproducibility_report.json"
    out_file.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify dual-run SHA-256 dataset reproducibility.")
    parser.add_argument("--profile", choices=["small", "medium", "full"], default="small")
    parser.add_argument("--seed", type=int, default=20261022)
    args = parser.parse_args()

    report = verify_reproducibility(profile=args.profile, seed=args.seed)
    print(json.dumps(report, indent=2))
    return 0 if report["is_reproducible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
