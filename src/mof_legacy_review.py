"""Produce a review-only MOF legacy delta from a blocked candidate.

Never alter the master, approval ledger, official snapshots, or active
screening files. This record remains evidence, not a delisting authority.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import master as M

ROOT = Path(__file__).resolve().parent.parent
OUT = Path("data/dashboard/mof_legacy_review.csv")
COLS = [
    "候補ID", "検知日時UTC", "状態", "対象者", "match_key",
    "変更フィールド", "変更前出典", "変更後出典",
    "変更前カテゴリ", "変更後カテゴリ", "変更前状態", "変更後状態",
    "旧master SHA256", "候補master SHA256", "財務省原本SHA256",
    "財務省原本URL", "実行URL",
]
REVIEW_FIELDS = ("sources", "categories", "status", "invalid_reason",
                 "risk_type", "risk_level", "review_flag", "display_name",
                 "variants", "first_seen_ms")


def _read_rows(body: str) -> dict[str, dict]:
    reader = csv.DictReader(io.StringIO(body, newline=""), strict=True)
    if reader.fieldnames != M.FIELDS:
        raise ValueError("MOF review master schema changed")
    rows = {}
    for row in reader:
        if None in row or None in row.values():
            raise ValueError("malformed MOF master candidate")
        key = row["match_key"]
        if not key or key in rows:
            raise ValueError("missing or duplicate MOF master match_key")
        rows[key] = row
    return rows


def _hash(row: dict) -> str:
    return hashlib.sha256(json.dumps(
        row, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()).hexdigest()


def _text(v: str) -> str:
    """Prevent spreadsheet formula execution when candidate CSV is imported."""
    v = str(v or "").replace("\x00", "").replace("\r", " ").replace("\n", " ")
    return "'" + v if v.lstrip().startswith(("=", "+", "-", "@")) else v


def build_review(base: dict[str, dict], candidate: dict[str, dict],
                 ledger: dict, source: dict, *, detected_at: str,
                 run_url: str = "") -> tuple[list[list[str]], dict]:
    if (ledger.get("state") != "ACTIVE"
            or not isinstance(ledger.get("entries"), list)
            or len(ledger["entries"]) != 2429):
        raise ValueError("active legacy acceptance is absent or changed")
    if not source.get("sha256") or not source.get("url"):
        raise ValueError("MOF candidate has no source document hash/URL")

    keys = [r.get("master_key") for r in ledger["entries"]]
    if len(set(keys)) != len(keys) or any(not k for k in keys):
        raise ValueError("duplicate or absent accepted legacy key")
    cases = []
    cosmetic = 0
    for key in sorted(keys):
        prior, current = base.get(key), candidate.get(key)
        if prior is None or current is None:
            raise ValueError(f"accepted legacy row missing: {key}")
        if not all("LEGACY_UNVERIFIED_V1:" in str(r.get("review_flag", ""))
                   for r in (prior, current)):
            raise ValueError(f"accepted legacy review annotation missing: {key}")
        changed = [col for col in REVIEW_FIELDS if prior[col] != current[col]]
        if not changed:
            if prior != current:
                cosmetic += 1  # remark and last_updated_ms only
            continue
        token = hashlib.sha256((key + "\n" + _hash(prior) + "\n" +
                                _hash(current) + "\n" + source["sha256"])
                               .encode()).hexdigest()[:20]
        cases.append([
            "MOF-LEGACY-" + token, detected_at, "REVIEW_REQUIRED",
            _text(current["display_name"]), _text(key),
            ";".join(changed), _text(prior["sources"]),
            _text(current["sources"]), _text(prior["categories"]),
            _text(current["categories"]), _text(prior["status"]),
            _text(current["status"]), _hash(prior), _hash(current),
            source["sha256"], _text(source["url"]), _text(run_url),
        ])
    return cases, {"pending_review": len(cases),
                   "annotation_only_changes": cosmetic,
                   "approved_legacy_count": len(keys)}


def run(root: Path = ROOT) -> dict:
    root = root.resolve()
    old = subprocess.run(
        ["git", "-C", str(root), "show", "HEAD:data/master/master.csv"],
        capture_output=True, check=True, text=True, encoding="utf-8",
    ).stdout
    base = _read_rows(old)
    candidate = _read_rows((root / "data/master/master.csv").read_text(encoding="utf-8"))
    ledger = json.loads((root / "data/review/integrity_legacy_acceptances.json")
                        .read_text(encoding="utf-8"))
    state = json.loads((root / "data/state.json").read_text(encoding="utf-8"))
    source = state.get("mof", {})
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    url = f"https://github.com/{repo}/actions/runs/{run_id}" if repo and run_id else ""
    cases, totals = build_review(
        base, candidate, ledger, source, detected_at=now, run_url=url)
    path = root / OUT
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write an explicit empty review table when no legacy bindings changed.
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(COLS)
        writer.writerows(cases)
    print(json.dumps(totals | {"review_csv": str(OUT),
                               "source_hash": source["sha256"],
                               "run_url": url}, ensure_ascii=False))
    return totals


if __name__ == "__main__":
    run()
