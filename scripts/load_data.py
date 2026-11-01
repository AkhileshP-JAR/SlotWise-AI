"""CLI script to batch-load a generated SlotWise AI dataset profile into SQLite or PostgreSQL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import DATA_DIR, load_domain_config  # noqa: E402
from backend.app.database import configure_default_engine  # noqa: E402
from backend.app.services.data_loader import DatabaseLoader  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch-load SlotWise AI dataset into database.")
    parser.add_argument(
        "--profile",
        choices=["small", "medium", "full"],
        default="small",
        help="Dataset profile to load.",
    )
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR, help="Root data directory.")
    parser.add_argument(
        "--database-url", type=str, default=None, help="Optional SQLAlchemy database URL override."
    )
    args = parser.parse_args()

    cfg = load_domain_config(args.profile)
    engine = configure_default_engine(database_url=args.database_url)
    loader = DatabaseLoader(engine=engine, batch_size=cfg.dataset.batch_size)
    load_stats = loader.load_profile_from_disk(profile=args.profile, data_root=args.data_dir, reset_existing=True)
    persisted_counts = loader.verify_database_counts()

    mismatches = {
        tbl: {"inserted": load_stats["inserted_counts"][tbl], "persisted": persisted_counts[tbl]}
        for tbl in persisted_counts
        if load_stats["inserted_counts"][tbl] != persisted_counts[tbl]
    }
    load_stats["count_verification"] = "PASS" if not mismatches else "FAIL"
    if mismatches:
        load_stats["mismatches"] = mismatches
    print(json.dumps(load_stats, indent=2))
    return 0 if not mismatches else 1


if __name__ == "__main__":
    raise SystemExit(main())
