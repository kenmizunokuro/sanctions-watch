"""Fail-closed review export for legacy source changes after MOF refresh.

This file is diagnostic evidence only. It never changes master, approval ledger,
delist statuses, source links, or the integrity gate.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from . import integrity_legacy as L
from . import master as M

OUT = Path("failure-evidence/mof-legacy-review.csv")
COLUMNS = ["match_key", "name", "classification", "change_fields",
           "old_sources", "new_sources", "old_categories", "new_categories",
           "old_status", "new_status", "old_last_updated", "new_last_updated",
           "decision", "original_review_id"]


def review_rows(master: dict[str, dict], scope: dict, ledger: dict) -> list[dict]:
    # Validate immutable original scope and approval dates, but never approve
    # changed candidates. All changes remain blocked by integrity_audit.
    accepted = L._validate(ledger, scope)
    results = []
    for entry in accepted.values():
        original = L.annotate(
            scope[entry["original_review_id"]]["finding"]["master"],
            entry["classification"], ledger["expires_at"],
        )
        candidate = master.get(entry["master_key"])
        if candidate == original:
            continue
        changed = [f for f in M.FIELDS
                   if candidate is None or str(candidate.get(f, "")) != str(original.get(f, ""))]
        results.append({
            "match_key": entry["master_key"],
            "name": original["display_name"],
            "classification": entry["classification"],
            "change_fields": ";".join(changed),
            "old_sources": original.get("sources", ""),
            "new_sources": (candidate or {}).get("sources", ""),
            "old_categories": original.get("categories", ""),
            "new_categories": (candidate or {}).get("categories", ""),
            "old_status": original.get("status", ""),
            "new_status": (candidate or {}).get("status", ""),
            "old_last_updated": original.get("last_updated_ms", ""),
            "new_last_updated": (candidate or {}).get("last_updated_ms", ""),
            "decision": "REVIEW_REQUIRED_NOT_APPLIED",
            "original_review_id": entry["original_review_id"],
        })
    return sorted(results, key=lambda row: row["match_key"])


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    try:
        scope = L.load_scope(root)
        ledger = json.loads((root / L.LEDGER_PATH).read_text(encoding="utf-8"))
        current = M.load(root / "data/master/master.csv")
        results = review_rows(current, scope, ledger)
        destination = root / OUT
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(results)
        print(json.dumps({
            "status": "REVIEW_REQUIRED" if results else "NO_LEGACY_CANDIDATE_CHANGES",
            "changed_approved_rows": len(results),
            "source_or_category_changed": sum(
                "sources" in x["change_fields"].split(";") or
                "categories" in x["change_fields"].split(";") for x in results
            ),
            "auto_approved": 0, "master_applied": False,
            "artifact": str(OUT),
        }, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("MOF LEGACY DIAGNOSTIC FAILED: " + str(exc), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
