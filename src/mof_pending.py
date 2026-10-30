"""MOF official record-diff sensor. Publish review candidates, never master.

This must run against the *uncommitted fetched candidate* before integrity
approval. The only durable output is a separate review CSV, not changes.csv,
master.csv, source_record_state, or the screening/export lists.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import mof_record_diff as R

PATH = Path("data/dashboard/mof_pending.csv")
COLUMNS = [
    "候補ID", "検知日時UTC", "出所", "種別", "対象者", "番号",
    "国連参照番号", "変更項目", "変更前", "変更後", "影響区分",
    "前回原本SHA256", "今回原本SHA256", "今回原本ファイル",
    "取得URL", "検証状態", "GitHub実行URL",
]
ALLOWED_KINDS = {"原本レコード追加", "原本レコード削除", "情報改訂"}
MAX_CANDIDATES = 20000
STATUS = "要レビュー・master未反映"


def _cell(value) -> str:
    value = str(value if value is not None else "").replace("\x00", "")
    value = value.replace("\r", " ").replace("\n", " ")
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def _case_id(previous_sha: str, current_sha: str, kind: str,
             record_id: str, field: str, before: str, after: str) -> str:
    payload = json.dumps(
        [previous_sha, current_sha, kind, record_id, field, before, after],
        ensure_ascii=False, separators=(",", ":"),
    )
    return "MOF-PENDING-" + hashlib.sha256(payload.encode()).hexdigest()[:24]


def candidate_rows(diffs: list[R.SourceDiff], *, detected_at: str,
                   source_url: str, run_url: str = "") -> list[list[str]]:
    if not source_url.startswith("https://www.mof.go.jp/"):
        raise R.RecordDiffError("MOF candidate must have a validated official URL")
    by_id = {}
    for d in diffs:
        old_sha = R._parse_csv(d.before_path).content_hash
        new_sha = R._parse_csv(d.after_path).content_hash
        records = []
        for item in d.added:
            records.append(("原本レコード追加", item, "", "", "", "要レビュー"))
        for item in d.removed:
            records.append(("原本レコード削除", item, "", "", "", "掲載終了候補・要レビュー"))
        for c in d.amended:
            record = {"番号": c.after_id or c.before_id,
                      "国連参照番号": c.un_reference, "_name": c.name}
            records.append(("情報改訂", record, c.field_name,
                            c.before, c.after, c.impact))
        for kind, record, field, before, after, impact in records:
            rid = record["番号"]
            name = record.get("_name") or R._display_name(record)
            # Include all original fields only in the archived official raw
            # file. Candidate CSV is review-only and bounded in size.
            ident = _case_id(old_sha, new_sha, kind, rid, field, before, after)
            row = [
                ident, detected_at, "財務省", kind, _cell(name),
                _cell(rid), _cell(record.get("国連参照番号", "")),
                _cell(field), _cell(before), _cell(after), impact,
                old_sha, new_sha, d.after_path.name,
                source_url, STATUS, run_url,
            ]
            if ident in by_id and by_id[ident] != row:
                raise R.RecordDiffError("same MOF pending candidate ID has conflicting details")
            by_id[ident] = row
    if len(by_id) > MAX_CANDIDATES:
        raise R.RecordDiffError("excessive MOF pending candidate count; manual review required")
    return [by_id[key] for key in sorted(by_id)]


def write_csv(root: Path, rows: list[list[str]]) -> Path:
    out = root / PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = out.with_name(out.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(COLUMNS)
        writer.writerows(rows)
    temp.replace(out)
    return out


def run(root: Path) -> dict:
    root = root.resolve()
    state = R._load_state(root)
    transitions, latest = R.plan_transitions(root, state)
    if latest is None:
        raise R.RecordDiffError("MOF raw data missing; no zero-change publication allowed")
    official = json.loads((root / "data/state.json").read_text(encoding="utf-8")).get("mof", {})
    newest = R._parse_csv(latest)
    if newest.content_hash != official.get("sha256"):
        raise R.RecordDiffError("MOF latest raw SHA256 differs from fetched state")
    url = official.get("url", "")
    if not url.startswith("https://www.mof.go.jp/"):
        raise R.RecordDiffError("MOF official URL absent or invalid")
    if not transitions and not state.get("processed_raw"):
        raise R.RecordDiffError("MOF first source baseline not verified; require manual baseline")
    diffs = [R.compare_snapshots(R._parse_csv(p), R._parse_csv(q))
             for p, q in transitions]
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    repo, github_run = os.getenv("GITHUB_REPOSITORY", ""), os.getenv("GITHUB_RUN_ID", "")
    run_url = (f"https://github.com/{repo}/actions/runs/{github_run}"
               if repo and github_run else "")
    rows = candidate_rows(diffs, detected_at=stamp, source_url=url, run_url=run_url)
    path = write_csv(root, rows)
    result = {
        "state": "PENDING_REVIEW" if rows else "NO_SOURCE_DIFF",
        "candidates": len(rows),
        "added": sum(len(d.added) for d in diffs),
        "removed": sum(len(d.removed) for d in diffs),
        "amended_records": len({
            (d.after_path.name, c.after_id or c.before_id)
            for d in diffs for c in d.amended
        }),
        "source_sha256": newest.content_hash, "candidate_path": str(path),
        "master_applied": False,
    }
    # A receipt is written only after parsing, hash checks and complete diff
    # generation succeed. It never advances the applied source baseline.
    receipt = {
        "schema_version": 1, "run_id": github_run,
        "run_attempt": os.getenv("GITHUB_RUN_ATTEMPT", ""),
        "run_url": run_url, "checked_at": stamp,
        "sha256": newest.content_hash, "url": url,
        "raw_path": latest.relative_to(root).as_posix(),
        "asof": official.get("asof", ""),
        "record_count": official.get("record_count", ""),
        "etag": official.get("etag", ""),
        "last_modified": official.get("last_modified", ""),
        "pending_count": len(rows), "master_applied": False,
        "pending_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    receipt_path = root / "data/monitoring/mof_candidate.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    temp = receipt_path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(receipt_path)
    print(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == "__main__":
    try:
        run(Path(__file__).resolve().parent.parent)
    except (R.RecordDiffError, OSError, ValueError, KeyError, TypeError) as exc:
        print("MOF CANDIDATE BLOCKED: " + str(exc), flush=True)
        raise SystemExit(1)
