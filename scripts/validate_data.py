"""CLI script to execute the 18-rule DatasetValidator against a generated SlotWise AI dataset."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import DATA_DIR, load_domain_config  # noqa: E402
from backend.app.validators.dataset_validator import DatasetValidator  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate generated SlotWise AI dataset.")
    parser.add_argument(
        "--profile",
        choices=["small", "medium", "full"],
        default="small",
        help="Dataset profile to validate.",
    )
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR, help="Root data directory.")
    args = parser.parse_args()

    cfg = load_domain_config(args.profile)
    validator = DatasetValidator(config=cfg, profile=args.profile)
    tables, gt_df = validator.load_tables_from_disk(profile=args.profile, data_root=args.data_dir)
    report = validator.validate(tables=tables, ground_truth_df=gt_df)
    report_path = validator.save_report(report, output_root=args.data_dir)

    output = {
        "profile": report["profile"],
        "is_valid": report["is_valid"],
        "summary": report["summary"],
        "checks": report["checks"],
        "report_path": report_path.as_posix(),
    }
    print(json.dumps(output, indent=2))
    if not report["is_valid"]:
        print("Unexpected validation errors:", json.dumps(report["errors"], indent=2), file=sys.stderr)
        print("Unmatched ground truth:", json.dumps(report["unmatched_ground_truth"], indent=2), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
