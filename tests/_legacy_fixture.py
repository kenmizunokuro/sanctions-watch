"""Shared historical 2026-10-26 -> 2026-11-25 legacy fixture for November 2026 tests."""
from __future__ import annotations

import copy
import functools
import json
from pathlib import Path

from src import integrity_legacy as L

HISTORICAL_REVIEWED_AT = '2026-10-26T08:21:15Z'
HISTORICAL_EXPIRES_AT = '2026-11-25T08:21:15Z'


@functools.lru_cache(maxsize=4)
def _cached_historical_pack(repo_root: str) -> tuple[dict, dict[str, dict]]:
    root = Path(repo_root)
    scope = L.load_scope(root)
    ledger = json.loads((root / L.LEDGER_PATH).read_bytes())
    pinned_ledger = copy.deepcopy(ledger)
    pinned_ledger['reviewed_at'] = HISTORICAL_REVIEWED_AT
    pinned_ledger['expires_at'] = HISTORICAL_EXPIRES_AT
    rebuilt_entries = []
    annotated_rows: dict[str, dict] = {}
    for entry in ledger['entries']:
        ident = entry['original_review_id']
        repaired = bool(entry.get('key_repaired', False))
        reclassified = bool(entry.get('notice_reclassified', False))
        rebuilt = L._entry(
            ident,
            scope[ident],
            HISTORICAL_EXPIRES_AT,
            key_repaired=repaired,
            notice_reclassified=reclassified,
            accepted_review_id=entry['accepted_review_id'] if reclassified else None,
            reviewed_evidence_sha256=entry['reviewed_evidence_sha256'] if reclassified else None,
        )
        rebuilt_entries.append(rebuilt)
        base_master = scope[ident]['finding']['master']
        if repaired:
            base_master = dict(base_master, match_key=rebuilt['master_key'])
        annotated_rows[rebuilt['master_key']] = L.annotate(
            base_master, rebuilt['classification'], HISTORICAL_EXPIRES_AT
        )
    pinned_ledger['entries'] = rebuilt_entries
    return pinned_ledger, annotated_rows


def pin_historical_legacy(root: Path, rows: dict, ledger: dict) -> tuple[dict, dict]:
    """Rebuild legacy rows and ledger entries against the historical Nov 2026 term."""
    repo_root = str(Path(__file__).resolve().parent.parent)
    cached_ledger, cached_rows = _cached_historical_pack(repo_root)
    for key, row in cached_rows.items():
        rows[key] = copy.deepcopy(row)
    return rows, copy.deepcopy(cached_ledger)
