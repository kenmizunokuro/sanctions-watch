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
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import master as M

ROOT = Path(__file__).resolve().parent.parent
OUT = Path("data/dashboard/mof_legacy_review.csv")
RECEIPT = Path("data/monitoring/mof_legacy_candidate.json")
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


def review_case(prior, current, source, *, detected_at, run_url):
    key = current["match_key"]
    changed = [col for col in REVIEW_FIELDS if prior[col] != current[col]]
    if not changed:
        return None
    before_hash, after_hash = _hash(prior), _hash(current)
    # Poll-time bookkeeping must not turn the same open change into a new case.
    # Keep full row hashes in the CSV for the latest audit's exact bindings.
    semantic = [_hash({f: row[f] for f in REVIEW_FIELDS}) for row in (prior, current)]
    token = hashlib.sha256("\n".join((key, *semantic, source["sha256"])).encode()).hexdigest()[:20]
    return [
        "MOF-LEGACY-" + token, detected_at, "REVIEW_REQUIRED",
        _text(current["display_name"]), _text(key),
        ";".join(changed), _text(prior["sources"]), _text(current["sources"]),
        _text(prior["categories"]), _text(current["categories"]),
        _text(prior["status"]), _text(current["status"]), before_hash, after_hash,
        source["sha256"], _text(source["url"]), _text(run_url),
    ]


def read_csv(root: Path) -> list[list[str]]:
    path = root / OUT
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        if next(reader, []) != COLS:
            raise ValueError("MOF legacy review schema mismatch")
        rows = list(reader)
    _indexed(rows)
    return rows


def _date(value: str) -> datetime:
    stamp = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if stamp.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ValueError("MOF legacy observation timestamp is invalid")
    return stamp


def _indexed(rows: list[list[str]]) -> dict[str, list[str]]:
    result = {}
    for row in rows:
        if (len(row) != len(COLS) or row[2] != "REVIEW_REQUIRED"
                or not re.fullmatch(r"MOF-LEGACY-[0-9a-f]{20}", row[0])
                or not row[3].strip() or not row[4].strip()
                or any(not re.fullmatch(r"[0-9a-f]{64}", row[n]) for n in (12, 13, 14))
                or not re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+/actions/runs/[0-9]+", row[16])
                or row[0] in result):
            raise ValueError("invalid or duplicate MOF legacy observation")
        _date(row[1])
        result[row[0]] = row
    return result


def retain_first_detection(current: list[list[str]], previous: list[list[str]]) -> list[list[str]]:
    """Preserve observations of open cases, with the current exact audit hashes."""
    _indexed(current)
    prior = _indexed(previous)
    # Upgrade old full-row-hash IDs only if every meaningful changed value is
    # present in the CSV and the exact prior master row is unchanged. Hidden
    # field changes cannot be inferred safely from an old projection.
    old_ids = {}
    for saved in previous:
        token = hashlib.sha256('\n'.join((saved[4], saved[12], saved[13], saved[14])).encode()).hexdigest()[:20]
        if (saved[0] == 'MOF-LEGACY-' + token
                and set(saved[5].split(';')) <= {'display_name', 'sources', 'categories', 'status'}):
            key = tuple(saved[2:12] + saved[14:16] + [saved[12]])
            if key in old_ids:
                raise ValueError('duplicate old MOF legacy observation')
            old_ids[key] = saved
    output = []
    for row in current:
        item = list(row)
        saved = prior.get(row[0])
        if saved is None:
            saved = old_ids.get(tuple(row[2:12] + row[14:16] + [row[12]]))
        if saved and saved[2:12] + saved[14:16] == row[2:12] + row[14:16]:
            if _date(saved[1]) > _date(row[1]):
                raise ValueError("MOF legacy first observation is in the future")
            if saved[16].rsplit('/actions/runs/', 1)[0] != row[16].rsplit('/actions/runs/', 1)[0]:
                raise ValueError('MOF legacy observation belongs to another repository')
            item[1], item[16] = saved[1], saved[16]
        output.append(item)
    return output


def write_generated(root: Path, rows: list[list[str]], *, generated_at: str,
                    run_id: str, run_attempt: str, run_url: str) -> None:
    """Write a receipt last so an interrupted/failed generator cannot clear a queue."""
    from .monitor_attempt import _write
    path = root / OUT
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, lineterminator="\n").writerows([COLS, *rows])
    _write(root / RECEIPT, dict(schema_version=1, generated_at=generated_at,
           run_id=run_id, run_attempt=run_attempt, run_url=run_url,
           count=len(rows), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))


def read_generated(root: Path, failure: dict) -> tuple[list[list[str]], dict]:
    receipt = json.loads((root / RECEIPT).read_bytes())
    fetched = failure.get("fetch") or {}
    if (not isinstance(receipt, dict) or not isinstance(fetched, dict)
            or receipt.get("schema_version") != 1
            or any(not receipt.get(k) or receipt[k] != failure.get(k) or receipt[k] != fetched.get(k)
                   for k in ("run_id", "run_attempt", "run_url"))
            or fetched.get("master_applied") is not False
            or not _date(fetched["checked_at"]) <= _date(receipt["generated_at"]) <= _date(failure["finished_at"])
            or hashlib.sha256((root / OUT).read_bytes()).hexdigest() != receipt.get("sha256")):
        raise ValueError("MOF legacy generation is not bound to this fetched attempt")
    rows = read_csv(root)
    if (len(rows) != receipt.get("count")
            or any(row[1] != receipt["generated_at"] or row[16] != receipt["run_url"]
                   or row[14] != fetched["sha256"] or row[15] != _text(fetched["url"])
                   for row in rows)):
        raise ValueError("MOF legacy CSV differs from its generation receipt")
    return rows, receipt


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
        case = review_case(prior, current, source, detected_at=detected_at, run_url=run_url)
        if case is None:
            if prior != current:
                cosmetic += 1  # remark and last_updated_ms only
            continue
        cases.append(case)
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
    write_generated(root, cases, generated_at=now, run_id=run_id,
                    run_attempt=os.environ.get("GITHUB_RUN_ATTEMPT", ""), run_url=url)
    print(json.dumps(totals | {"review_csv": str(OUT),
                               "source_hash": source["sha256"],
                               "run_url": url}, ensure_ascii=False))
    return totals


if __name__ == "__main__":
    run()
