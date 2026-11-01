"""Independent database integrity & business-rule auditor for SlotWise AI.

This script deliberately uses RAW SQL via the standard-library `sqlite3` module and re-implements every rule
from scratch. It does NOT import any SlotWise service, repository, or compatibility module, so it can catch
defects that the application's own validators would share.

Usage:
    python scripts/audit_integrity.py --db data/slotwise.db [--expect-post-optimization] [--json out.json]

Exit code is 0 when every HARD check passes, 1 otherwise. INFO checks are reported but never fail the run.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any

TOL_W = 1e-3
TOL_V = 1e-5


class Auditor:
    def __init__(self, db_path: Path, post_opt: bool) -> None:
        self.con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        self.con.row_factory = sqlite3.Row
        self.post_opt = post_opt
        self.results: list[dict[str, Any]] = []

    def q(self, sql: str, *params: Any) -> list[sqlite3.Row]:
        return self.con.execute(sql, params).fetchall()

    def scalar(self, sql: str, *params: Any) -> Any:
        row = self.con.execute(sql, params).fetchone()
        return row[0] if row else None

    def record(self, name: str, ok: bool, detail: Any, hard: bool = True) -> None:
        self.results.append(
            {
                "check": name,
                "status": "PASS" if ok else ("FAIL" if hard else "WARN"),
                "hard": hard,
                "detail": detail,
            }
        )

    # ---------------------------------------------------------------- checks
    def check_sqlite_integrity(self) -> None:
        self.record(
            "sqlite_integrity_check",
            self.scalar("PRAGMA integrity_check") == "ok",
            self.scalar("PRAGMA integrity_check"),
        )
        fk = self.q("PRAGMA foreign_key_check")
        self.record("sqlite_foreign_key_check", len(fk) == 0, f"{len(fk)} orphan FK rows")

    def check_counts(self) -> None:
        tables = [
            r[0]
            for r in self.q(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        counts = {t: self.scalar(f'SELECT COUNT(*) FROM "{t}"') for t in tables}
        self.record("table_counts", all(v is not None for v in counts.values()) and len(tables) >= 21, counts)

    def check_ground_truth_separation(self) -> None:
        forbidden = {"ground_truth", "is_anomaly", "anomaly_id", "expected_issue_type", "injected", "is_injected"}
        leaks: list[str] = []
        for t in [r[0] for r in self.q("SELECT name FROM sqlite_master WHERE type='table'")]:
            for col in self.q(f'PRAGMA table_info("{t}")'):
                if col["name"].lower() in forbidden:
                    leaks.append(f"{t}.{col['name']}")
        self.record("ground_truth_columns_absent_from_db", not leaks, leaks or "no forbidden columns")
        # Source-level check: detector must never read the ground_truth directory.
        engine_src = Path(__file__).resolve().parents[1] / "backend/app/services/exception_engine.py"
        text = engine_src.read_text(encoding="utf-8")
        self.record(
            "exception_engine_never_references_ground_truth", "ground_truth" not in text, str(engine_src.name)
        )

    def check_assignments(self) -> None:
        # Single ACTIVE per SKU
        multi_sku = self.q(
            "SELECT sku_id, COUNT(*) c FROM slot_assignments WHERE assignment_status='ACTIVE' GROUP BY sku_id HAVING c>1"
        )
        dedicated_multi = self.q(
            """SELECT sa.location_id, COUNT(*) c FROM slot_assignments sa JOIN locations l ON l.location_id=sa.location_id
               WHERE sa.assignment_status='ACTIVE' AND l.storage_policy='DEDICATED_SINGLE_SKU'
               GROUP BY sa.location_id HAVING c>1"""
        )
        no_active = self.scalar(
            """SELECT COUNT(*) FROM skus s WHERE s.status='ACTIVE'
               AND NOT EXISTS (SELECT 1 FROM slot_assignments a WHERE a.sku_id=s.sku_id AND a.assignment_status='ACTIVE')"""
        )
        hard = self.post_opt
        self.record(
            "active_assignment_unique_per_sku",
            not multi_sku,
            {"skus_with_multiple_active": [r[0] for r in multi_sku]},
            hard,
        )
        self.record(
            "dedicated_location_single_active_sku",
            not dedicated_multi,
            {"locations": [r[0] for r in dedicated_multi]},
            hard,
        )
        self.record("active_skus_without_active_assignment", no_active == 0, {"count": no_active}, False)

        # Physical compatibility of every ACTIVE row, re-derived independently
        rows = self.q(
            """SELECT sa.slot_assignment_id, sa.sku_id, sa.location_id, sa.assigned_quantity, sa.allocated_weight_kg,
                      sa.allocated_volume_m3, s.width_cm sw, s.height_cm sh, s.depth_cm sd, s.unit_weight_kg, s.hazard_class,
                      s.temperature_requirement, s.storage_type,
                      l.width_cm lw, l.height_cm lh, l.depth_cm ld, l.is_blocked, l.status lstatus, l.zone_id, l.aisle_id,
                      z.temperature_type, z.zone_type, z.status zstatus, a.is_blocked ablocked
               FROM slot_assignments sa JOIN skus s ON s.sku_id=sa.sku_id JOIN locations l ON l.location_id=sa.location_id
               JOIN zones z ON z.zone_id=l.zone_id JOIN aisles a ON a.aisle_id=l.aisle_id
               WHERE sa.assignment_status='ACTIVE'"""
        )
        allowed = {
            (r["zone_id"], r["hazard_class_id"])
            for r in self.q("SELECT zone_id, hazard_class_id FROM zone_hazard_allowances")
        }
        viol: dict[str, list[str]] = {
            "dimension": [],
            "temperature": [],
            "hazard": [],
            "blocked": [],
            "alloc_weight": [],
            "alloc_volume": [],
        }
        for r in rows:
            sid = r["slot_assignment_id"]
            if r["sw"] > r["lw"] + 1e-6 or r["sh"] > r["lh"] + 1e-6 or r["sd"] > r["ld"] + 1e-6:
                viol["dimension"].append(sid)
            if r["temperature_requirement"] != r["temperature_type"]:
                viol["temperature"].append(sid)
            if (r["zone_id"], r["hazard_class"]) not in allowed:
                viol["hazard"].append(sid)
            if r["is_blocked"] or r["ablocked"] or r["lstatus"] != "ACTIVE" or r["zstatus"] != "ACTIVE":
                viol["blocked"].append(sid)
            exp_w = r["assigned_quantity"] * r["unit_weight_kg"]
            exp_v = r["assigned_quantity"] * r["sw"] * r["sh"] * r["sd"] / 1_000_000.0
            if abs(exp_w - r["allocated_weight_kg"]) > max(0.01, 1e-3 * exp_w):
                viol["alloc_weight"].append(sid)
            if abs(exp_v - r["allocated_volume_m3"]) > max(1e-4, 1e-3 * exp_v):
                viol["alloc_volume"].append(sid)
        for k, v in viol.items():
            self.record(
                f"active_assignment_{k}_valid", not v, {"violations": len(v), "sample": v[:5]}, self.post_opt
            )

        # Pairwise hazard/category co-location conflicts (from compatibility_rules table)
        rules = self.q(
            "SELECT rule_type, primary_attribute, conflicting_attribute FROM compatibility_rules WHERE is_hard_constraint=1"
        )
        haz_pairs = {(r[1], r[2]) for r in rules if r[0] == "HAZARD_CO_LOCATION"} | {
            (r[2], r[1]) for r in rules if r[0] == "HAZARD_CO_LOCATION"
        }
        cat_pairs = {(r[1], r[2]) for r in rules if r[0] == "CATEGORY_CO_LOCATION"} | {
            (r[2], r[1]) for r in rules if r[0] == "CATEGORY_CO_LOCATION"
        }
        by_loc: dict[str, list[sqlite3.Row]] = {}
        for r in self.q(
            "SELECT sa.location_id, s.sku_id, s.hazard_class, s.category FROM slot_assignments sa JOIN skus s ON s.sku_id=sa.sku_id WHERE sa.assignment_status='ACTIVE'"
        ):
            by_loc.setdefault(r["location_id"], []).append(r)
        conflicts = []
        for lid, lst in by_loc.items():
            for i in range(len(lst)):
                for j in range(i + 1, len(lst)):
                    if (lst[i]["hazard_class"], lst[j]["hazard_class"]) in haz_pairs or (
                        lst[i]["category"],
                        lst[j]["category"],
                    ) in cat_pairs:
                        conflicts.append(lid)
        self.record(
            "active_colocation_conflicts", not conflicts, {"locations": sorted(set(conflicts))}, self.post_opt
        )

    def check_capacity_reconciliation(self) -> None:
        rows = self.q(
            """SELECT l.location_id, l.max_weight_kg, l.max_volume_m3, l.current_weight_kg, l.current_volume_m3,
                      COALESCE(SUM(sa.allocated_weight_kg),0) aw, COALESCE(SUM(sa.allocated_volume_m3),0) av
               FROM locations l LEFT JOIN slot_assignments sa ON sa.location_id=l.location_id AND sa.assignment_status='ACTIVE'
               GROUP BY l.location_id"""
        )
        drift = [
            r["location_id"]
            for r in rows
            if abs(r["current_weight_kg"] - r["aw"]) > 0.01 or abs(r["current_volume_m3"] - r["av"]) > 1e-4
        ]
        over = [
            r["location_id"]
            for r in rows
            if r["aw"] > r["max_weight_kg"] + TOL_W or r["av"] > r["max_volume_m3"] + TOL_V
        ]
        self.record(
            "location_current_load_equals_sum_of_active_allocations",
            not drift,
            {"drifting_locations": len(drift), "sample": drift[:8]},
            self.post_opt,
        )
        self.record(
            "active_allocations_within_location_capacity",
            not over,
            {"over_capacity_locations": over},
            self.post_opt,
        )

    def check_assignment_history(self) -> None:
        bad_active = self.scalar(
            "SELECT COUNT(*) FROM slot_assignments WHERE assignment_status='ACTIVE' AND effective_to IS NOT NULL"
        )
        bad_sup = self.scalar(
            "SELECT COUNT(*) FROM slot_assignments WHERE assignment_status IN ('SUPERSEDED','REJECTED') AND effective_to IS NULL"
        )
        self.record(
            "lifecycle_effective_to_consistent",
            bad_active == 0 and bad_sup == 0,
            {"active_with_end": bad_active, "closed_without_end": bad_sup},
        )
        # decision_version must be unique and increase with slot_assignment_id per SKU
        rows = self.q(
            "SELECT sku_id, slot_assignment_id, decision_version FROM slot_assignments ORDER BY sku_id, slot_assignment_id"
        )
        last: dict[str, int] = {}
        nonmono: list[str] = []
        for r in rows:
            if r["decision_version"] <= last.get(r["sku_id"], 0) and r["decision_version"] != 1:
                nonmono.append(r["slot_assignment_id"])
            last[r["sku_id"]] = max(last.get(r["sku_id"], 0), r["decision_version"])
        dup_ver = self.q(
            "SELECT sku_id, decision_version, COUNT(*) c FROM slot_assignments GROUP BY sku_id, decision_version HAVING c>1"
        )
        self.record(
            "decision_version_unique_per_sku",
            not dup_ver,
            {"duplicates": [(r[0], r[1]) for r in dup_ver][:5]},
            False,
        )
        self.record("decision_version_monotonic", not nonmono, {"violations": nonmono[:5]}, False)
        n_hist = self.scalar("SELECT COUNT(*) FROM slot_assignments WHERE assignment_status='SUPERSEDED'")
        self.record(
            "history_preserved_info",
            True,
            {"superseded_rows": n_hist, "total_rows": self.scalar("SELECT COUNT(*) FROM slot_assignments")},
            False,
        )
        orphan_reason = self.scalar(
            "SELECT COUNT(*) FROM assignment_reasons r WHERE r.slot_assignment_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM slot_assignments s WHERE s.slot_assignment_id=r.slot_assignment_id)"
        )
        self.record(
            "assignment_reasons_reference_existing_assignments", orphan_reason == 0, {"orphans": orphan_reason}
        )
        unexplained = self.scalar(
            """SELECT COUNT(*) FROM slot_assignments s WHERE s.optimization_run_id IS NOT NULL AND NOT EXISTS
               (SELECT 1 FROM assignment_reasons r WHERE r.slot_assignment_id=s.slot_assignment_id AND r.is_selected=1)"""
        )
        self.record(
            "every_optimized_assignment_has_decision_record",
            unexplained == 0,
            {"assignments_without_selected_reason": unexplained},
        )
        rej_no_reason = self.scalar(
            "SELECT COUNT(*) FROM assignment_reasons WHERE is_feasible=0 AND (rejection_reasons_json IS NULL OR rejection_reasons_json='[]')"
        )
        self.record(
            "every_rejected_candidate_has_reason", rej_no_reason == 0, {"rejected_without_reason": rej_no_reason}
        )

    def check_orders_and_events(self) -> None:
        arith = self.scalar(
            "SELECT COUNT(*) FROM order_lines WHERE ABS(line_value - ROUND(quantity*unit_price,2)) > 0.011"
        )
        price_mismatch = self.scalar(
            "SELECT COUNT(*) FROM order_lines ol JOIN skus s ON s.sku_id=ol.sku_id WHERE ABS(ol.unit_price - s.unit_price) > 0.011"
        )
        self.record("order_line_arithmetic", arith == 0, {"bad_rows": arith})
        self.record(
            "order_line_price_matches_sku_price", price_mismatch == 0, {"mismatches": price_mismatch}, False
        )
        over_pick = self.scalar(
            "SELECT COUNT(*) FROM pick_events pe JOIN order_lines ol ON ol.order_line_id=pe.order_line_id WHERE pe.quantity_picked > ol.quantity"
        )
        self.record("pick_qty_not_exceeding_order_qty", over_pick == 0, {"violations": over_pick})
        early = self.scalar(
            "SELECT COUNT(*) FROM pick_events pe JOIN orders o ON o.order_id=pe.order_id WHERE pe.pick_timestamp < o.order_date"
        )
        self.record("pick_after_order_timestamp", early == 0, {"violations": early})
        lines_wo_orders = self.scalar(
            "SELECT COUNT(*) FROM order_lines ol WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.order_id=ol.order_id)"
        )
        self.record("no_orphan_order_lines", lines_wo_orders == 0, {"orphans": lines_wo_orders})
        empty_orders = self.scalar(
            "SELECT COUNT(*) FROM orders o WHERE NOT EXISTS (SELECT 1 FROM order_lines ol WHERE ol.order_id=o.order_id)"
        )
        self.record("orders_have_lines", empty_orders == 0, {"orders_without_lines": empty_orders}, False)

    def check_profiles(self) -> None:
        rows = self.q(
            """SELECT p.sku_id, p.total_units_sold, p.total_order_lines, p.average_daily_demand, p.reorder_point, p.safety_stock,
                      p.demand_std_dev, su.average_lead_time_days lt,
                      (SELECT COALESCE(SUM(ol.quantity),0) FROM order_lines ol WHERE ol.sku_id=p.sku_id) units,
                      (SELECT COUNT(*) FROM order_lines ol WHERE ol.sku_id=p.sku_id) nlines
               FROM sku_profiles p JOIN skus s ON s.sku_id=p.sku_id LEFT JOIN suppliers su ON su.supplier_id=s.supplier_id
               WHERE p.effective_to IS NULL"""
        )
        days = (
            self.scalar(
                "SELECT CAST(julianday(MAX(date(order_date))) - julianday(MIN(date(order_date))) AS INT) + 1 FROM orders"
            )
            or 90
        )
        bad_units = [
            r["sku_id"]
            for r in rows
            if r["units"] != r["total_units_sold"] or r["nlines"] != r["total_order_lines"]
        ]
        bad_adv = [
            r["sku_id"] for r in rows if abs(r["total_units_sold"] / 90.0 - r["average_daily_demand"]) > 1e-4
        ]
        bad_rop = []
        for r in rows:
            lt = r["lt"] if r["lt"] is not None else 5.0
            exp_rop = r["average_daily_demand"] * lt + 1.645 * r["demand_std_dev"] * math.sqrt(lt)
            if abs(exp_rop - r["reorder_point"]) > 0.01:
                bad_rop.append(r["sku_id"])
        self.record(
            "profile_units_and_lines_match_order_lines",
            not bad_units,
            {"mismatches": len(bad_units), "sample": bad_units[:5], "observed_days": days},
        )
        self.record("profile_adv_equals_units_over_90_days", not bad_adv, {"mismatches": len(bad_adv)})
        self.record(
            "profile_rop_formula_recomputed", not bad_rop, {"mismatches": len(bad_rop), "sample": bad_rop[:5]}
        )

    def check_exceptions(self) -> None:
        orphan = []
        for r in self.q("SELECT exception_id, entity_type, entity_id FROM exceptions"):
            tbl = {
                "LOCATION": ("locations", "location_id"),
                "SKU": ("skus", "sku_id"),
                "WAREHOUSE": ("warehouses", "warehouse_id"),
                "SLOT_ASSIGNMENT": ("slot_assignments", "slot_assignment_id"),
                "PICK_EVENT": ("pick_events", "pick_event_id"),
                "AISLE": ("aisles", "aisle_id"),
                "ORDER": ("orders", "order_id"),
            }.get(r["entity_type"])
            if tbl and self.scalar(f'SELECT COUNT(*) FROM "{tbl[0]}" WHERE "{tbl[1]}"=?', r["entity_id"]) == 0:
                orphan.append(r["exception_id"])
        self.record("exceptions_reference_existing_entities", not orphan, {"orphans": orphan[:5]})
        by_type = {r[0]: r[1] for r in self.q("SELECT issue_type, COUNT(*) FROM exceptions GROUP BY issue_type")}
        self.record("exception_counts_info", True, by_type, False)

    def check_replenishment(self) -> None:
        over = self.q(
            """SELECT r.warehouse_id, SUM(r.assigned_labor_minutes) a, w.daily_labor_minutes_capacity cap
               FROM replenishments r JOIN warehouses w ON w.warehouse_id=r.warehouse_id GROUP BY r.warehouse_id"""
        )
        info = {r["warehouse_id"]: {"assigned": round(r["a"], 1), "capacity": r["cap"]} for r in over}
        self.record("replenishment_labor_by_warehouse_info", True, info, False)
        bad_status = self.scalar(
            "SELECT COUNT(*) FROM replenishments WHERE replenishment_status='OVERLOADED' AND assigned_labor_minutes >= labor_minutes_required"
        )
        self.record("overloaded_tasks_truly_short_of_labor", bad_status == 0, {"inconsistent": bad_status})
        neg = self.scalar("SELECT COUNT(*) FROM replenishments WHERE replenishment_quantity > required_quantity")
        self.record("replenishment_qty_not_above_required", neg == 0, {"violations": neg})

    def check_realism(self) -> None:
        stats: dict[str, Any] = {}
        units = [
            r[0]
            for r in self.q(
                "SELECT total_units_sold FROM sku_profiles WHERE effective_to IS NULL ORDER BY total_units_sold DESC"
            )
        ]
        tot = sum(units) or 1
        top20 = sum(units[: max(1, len(units) // 5)]) / tot
        stats["top20pct_skus_unit_share"] = round(top20, 3)
        stats["zero_demand_skus"] = sum(1 for u in units if u == 0)
        stats["lines_per_order_avg"] = round(
            self.scalar("SELECT 1.0*COUNT(*)/COUNT(DISTINCT order_id) FROM order_lines") or 0, 2
        )
        stats["hazmat_sku_share"] = round(
            self.scalar("SELECT 1.0*SUM(hazard_class!='NONE')/COUNT(*) FROM skus") or 0, 3
        )
        stats["non_ambient_sku_share"] = round(
            self.scalar("SELECT 1.0*SUM(temperature_requirement!='AMBIENT')/COUNT(*) FROM skus") or 0, 3
        )
        stats["unit_weight_kg_min_med_max"] = [
            round(float(x), 3)
            for x in (
                self.scalar("SELECT MIN(unit_weight_kg) FROM skus"),
                self.scalar(
                    "SELECT unit_weight_kg FROM skus ORDER BY unit_weight_kg LIMIT 1 OFFSET (SELECT COUNT(*)/2 FROM skus)"
                ),
                self.scalar("SELECT MAX(unit_weight_kg) FROM skus"),
            )
        ]
        stats["blocked_locations"] = self.scalar("SELECT COUNT(*) FROM locations WHERE is_blocked=1")
        stats["blocked_aisles"] = self.scalar("SELECT COUNT(*) FROM aisles WHERE is_blocked=1")
        stats["orders_per_day_min_max"] = [
            self.scalar("SELECT MIN(c) FROM (SELECT COUNT(*) c FROM orders GROUP BY date(order_date))"),
            self.scalar("SELECT MAX(c) FROM (SELECT COUNT(*) c FROM orders GROUP BY date(order_date))"),
        ]
        self.record("synthetic_realism_summary_info", True, stats, False)
        # Heavy-tail demand expected for warehouse data (Pareto-like): top 20% SKUs should carry >= 40% units
        self.record("demand_is_heavy_tailed", top20 >= 0.40, {"top20pct_share": round(top20, 3)}, False)

    def run(self) -> list[dict[str, Any]]:
        self.check_sqlite_integrity()
        self.check_counts()
        self.check_ground_truth_separation()
        self.check_assignments()
        self.check_capacity_reconciliation()
        self.check_assignment_history()
        self.check_orders_and_events()
        self.check_profiles()
        self.check_exceptions()
        self.check_replenishment()
        self.check_realism()
        return self.results


def main() -> int:
    ap = argparse.ArgumentParser(description="Independent raw-SQL integrity auditor.")
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument(
        "--expect-post-optimization",
        action="store_true",
        help="Treat assignment/capacity rule violations as HARD failures.",
    )
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()

    aud = Auditor(args.db, post_opt=args.expect_post_optimization)
    results = aud.run()
    hard_fail = [r for r in results if r["status"] == "FAIL"]
    warn = [r for r in results if r["status"] == "WARN"]
    for r in results:
        print(f"[{r['status']:<4}] {r['check']}: {json.dumps(r['detail'], default=str)[:300]}")
    print(f"\nSUMMARY: {len(results) - len(hard_fail) - len(warn)} pass, {len(warn)} warn, {len(hard_fail)} FAIL")
    if args.json:
        args.json.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    return 1 if hard_fail else 0


if __name__ == "__main__":
    sys.exit(main())
