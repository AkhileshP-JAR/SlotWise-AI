"""CLI script to generate deterministic synthetic datasets for SlotWise AI."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import DATA_DIR, load_domain_config  # noqa: E402
from backend.app.services.data_generator import SyntheticDataGenerator  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate SlotWise AI synthetic dataset.")
    parser.add_argument(
        "--profile",
        choices=["small", "medium", "full"],
        default="small",
        help="Dataset scale profile (default: small).",
    )
    parser.add_argument(
        "--seed", type=int, default=None, help="Override deterministic RNG seed (default: 20261022)."
    )
    parser.add_argument("--output-dir", type=Path, default=DATA_DIR, help="Target data directory.")
    parser.add_argument("--no-anomalies", action="store_true", help="Disable labelled anomaly injection.")
    args = parser.parse_args()

    cfg = load_domain_config(args.profile)
    if args.no_anomalies:
        cfg.dataset.anomaly_injection.enabled = False

    t0 = time.perf_counter()
    generator = SyntheticDataGenerator(config=cfg, profile=args.profile, seed=args.seed)
    bundle = generator.generate()
    manifest = generator.export_bundle(bundle, output_root=args.output_dir)
    elapsed = round(time.perf_counter() - t0, 3)

    summary = {
        "status": "SUCCESS",
        "profile": bundle.profile,
        "seed": bundle.seed,
        "elapsed_seconds": elapsed,
        "entity_counts": manifest["entity_counts"],
        "ground_truth_anomaly_count": manifest["ground_truth_anomaly_count"],
        "ground_truth_breakdown": manifest["ground_truth_breakdown"],
        "manifest_file": str((args.output_dir / "manifests" / f"{bundle.profile}_manifest.json").as_posix()),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
