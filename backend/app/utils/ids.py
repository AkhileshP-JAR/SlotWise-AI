"""Deterministic ID generators, pattern validators, and SHA-256 checksum utilities.

Conforms strictly to Section 6 ID Conventions:
- Warehouse: WH-001
- Location: LOC-<zone-id>-000001
- SKU: SKU-000001
- Order: ORD-2026-000001
- Pick event: PICK-<order-id>-001
- Slot assignment: SLOT-2026-000001
- Replenishment: REP-2026-000001
- Optimization run: OPT-2026-000001
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

ID_PATTERNS: dict[str, re.Pattern[str]] = {
    "warehouse_id": re.compile(r"^WH-\d{3}$"),
    "zone_id": re.compile(r"^Z\d{3}$"),
    "aisle_id": re.compile(r"^AISLE-Z\d{3}-\d{2}$"),
    "location_id": re.compile(r"^LOC-Z\d{3}-\d{6}$"),
    "supplier_id": re.compile(r"^SUP-\d{4}$"),
    "sku_id": re.compile(r"^SKU-\d{6}$"),
    "profile_id": re.compile(r"^PRF-SKU-\d{6}-\d{2}$"),
    "order_id": re.compile(r"^ORD-\d{4}-\d{6}$"),
    "order_line_id": re.compile(r"^OL-ORD-\d{4}-\d{6}-\d{3}$"),
    "picker_id": re.compile(r"^PKR-WH\d{3}-\d{3}$"),
    "pick_event_id": re.compile(r"^PICK-ORD-\d{4}-\d{6}-\d{3}$"),
    "slot_assignment_id": re.compile(r"^SLOT-\d{4}-\d{6}$"),
    "replenishment_id": re.compile(r"^REP-\d{4}-\d{6}$"),
    "optimization_run_id": re.compile(r"^OPT-\d{4}-\d{6}$"),
    "exception_id": re.compile(r"^EXC-\d{4}-\d{6}$"),
    "audit_event_id": re.compile(r"^AUD-\d{4}-\d{6}$"),
    "scenario_id": re.compile(r"^SCN-\d{4}-\d{5}$"),
    "rule_id": re.compile(r"^RULE-\d{3}$"),
}


def format_warehouse_id(seq: int) -> str:
    return f"WH-{seq:03d}"


def format_zone_id(seq: int) -> str:
    return f"Z{seq:03d}"


def format_aisle_id(zone_id: str, seq: int) -> str:
    return f"AISLE-{zone_id}-{seq:02d}"


def format_location_id(zone_id: str, seq: int) -> str:
    return f"LOC-{zone_id}-{seq:06d}"


def format_supplier_id(seq: int) -> str:
    return f"SUP-{seq:04d}"


def format_sku_id(seq: int) -> str:
    return f"SKU-{seq:06d}"


def format_profile_id(sku_id: str, version: int = 1) -> str:
    return f"PRF-{sku_id}-{version:02d}"


def format_order_id(year: int, seq: int) -> str:
    return f"ORD-{year:04d}-{seq:06d}"


def format_order_line_id(order_id: str, line_seq: int) -> str:
    return f"OL-{order_id}-{line_seq:03d}"


def format_picker_id(warehouse_id: str, seq: int) -> str:
    compact_wh = warehouse_id.replace("-", "")
    return f"PKR-{compact_wh}-{seq:03d}"


def format_pick_event_id(order_id: str, seq: int) -> str:
    return f"PICK-{order_id}-{seq:03d}"


def format_slot_assignment_id(year: int, seq: int) -> str:
    return f"SLOT-{year:04d}-{seq:06d}"


def format_replenishment_id(year: int, seq: int) -> str:
    return f"REP-{year:04d}-{seq:06d}"


def format_optimization_run_id(year: int, seq: int) -> str:
    return f"OPT-{year:04d}-{seq:06d}"


def format_exception_id(year: int, seq: int) -> str:
    return f"EXC-{year:04d}-{seq:06d}"


def format_audit_event_id(year: int, seq: int) -> str:
    return f"AUD-{year:04d}-{seq:06d}"


def format_scenario_id(year: int, seq: int) -> str:
    return f"SCN-{year:04d}-{seq:05d}"


def format_rule_id(seq: int) -> str:
    return f"RULE-{seq:03d}"


def format_reason_id(run_id: str, seq: int) -> str:
    return f"RSN-{run_id}-{seq:06d}"


def extract_trailing_sequence(identifier: str | None) -> int:
    """Extract the trailing integer counter from a deterministic SlotWise ID (e.g. 'SLOT-2026-000254' -> 254)."""
    if not identifier:
        return 0
    tail = identifier.rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def validate_id_format(id_type: str, value: str) -> bool:
    """Verify whether an identifier conforms to its deterministic regex convention."""
    pattern = ID_PATTERNS.get(id_type)
    if pattern is None:
        raise KeyError(f"Unknown ID pattern key: {id_type}")
    return bool(pattern.match(value))


def compute_file_sha256(file_path: Path) -> str:
    """Compute deterministic SHA-256 hex digest of a file normalizing CRLF to LF for text files."""
    hasher = hashlib.sha256()
    text_suffixes = {".csv", ".json", ".sql", ".yaml", ".yml", ".md"}
    if file_path.suffix.lower() in text_suffixes:
        raw_bytes = file_path.read_bytes().replace(b"\r\n", b"\n")
        hasher.update(raw_bytes)
    else:
        with file_path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                hasher.update(chunk)
    return hasher.hexdigest()
