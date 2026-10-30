"""Persist an unsuccessful MOF attempt without publishing unverified business data.

Run only after a failed watch-jp job. This is failure evidence, never a list
update, delisting decision or replacement for the integrity gate.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import dashboard as D
from . import state as S

ROOT = Path(__file__).resolve().parent.parent
FAILURE_PATH = Path("data/monitoring/mof_failure.json")
STATUS_PATH = Path("data/dashboard/status.csv")
REVIEW_PATH = Path("data/dashboard/mof_legacy_review.csv")
PENDING_PATH = Path("data/dashboard/mof_pending.csv")


def _review_case_count(path: Path) -> int:
    from .mof_legacy_review import COLS
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.reader(f, strict=True)
        if next(reader, []) != COLS:
            raise ValueError("MOF legacy review schema mismatch")
        rows = list(reader)
    if any(len(row) != len(COLS) or row[2] != "REVIEW_REQUIRED"
           or not row[0].startswith("MOF-LEGACY-") for row in rows):
        raise ValueError("invalid MOF review-only candidate")
    if len({row[0] for row in rows}) != len(rows):
        raise ValueError("duplicate MOF review candidate ID")
    return len(rows)


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=check, capture_output=True, text=True,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _candidate_fetch(root: Path, run_id: str, attempt: str, run_url: str) -> dict:
    from .mof_pending_gate import pending_count
    path = root / "data/monitoring/mof_candidate.json"
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if (receipt.get("schema_version") != 1 or not run_id or not attempt
            or receipt.get("run_id") != run_id or receipt.get("run_attempt") != attempt
            or receipt.get("run_url") != run_url or receipt.get("master_applied") is not False
            or not str(receipt.get("url", "")).startswith("https://www.mof.go.jp/")):
        raise ValueError("MOF fetch receipt is not bound to this run/attempt")
    datetime.strptime(receipt["checked_at"], "%Y-%m-%dT%H:%M:%SZ")
    raw = (root / receipt["raw_path"]).resolve()
    if not raw.is_relative_to((root / "data/raw/mof").resolve()):
        raise ValueError("MOF fetch receipt raw path escapes source archive")
    if hashlib.sha256(gzip.decompress(raw.read_bytes())).hexdigest() != receipt["sha256"]:
        raise ValueError("MOF fetched raw hash differs from receipt")
    if (hashlib.sha256((root / PENDING_PATH).read_bytes()).hexdigest() != receipt["pending_sha256"]
            or pending_count(root) != receipt["pending_count"]):
        raise ValueError("MOF pending evidence differs from validated fetch")
    return receipt


def _blocked_integrity(root: Path, fetched: dict) -> dict:
    """A process crash is not a review decision; require an actual bound report."""
    master_sha = hashlib.sha256((root / "data/master/master.csv").read_bytes()).hexdigest()
    receipt_time = (root / "data/monitoring/mof_candidate.json").stat().st_mtime_ns
    for path in sorted((root / "data/integrity_audit").glob("*.json.gz"), reverse=True):
        if path.name.startswith("triage-") or path.stat().st_mtime_ns < receipt_time:
            continue
        report = json.loads(gzip.decompress(path.read_bytes()))
        if (report.get("status") != "BLOCKED" or report.get("publication_allowed") is not False
                or report.get("inputs", {}).get("data/master/master.csv") != master_sha
                or not any(d.get("source") == "mof" and d.get("sha256") == fetched["sha256"]
                           for d in report.get("documents", []))):
            continue
        return {
            "status": "BLOCKED", "publication_allowed": False,
            "report": path.relative_to(root).as_posix(),
            "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "counts": report.get("counts", {}),
            "reasons": [{"kind": f.get("kind", ""), "detail": str(f.get("detail", ""))[:500]}
                        for f in report.get("findings", [])
                        if f.get("blocking") and f.get("detail")][:10],
        }
    raise ValueError("no current candidate-bound BLOCKED integrity report")


def failure_record() -> dict:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run = os.environ.get("GITHUB_RUN_ID", "")
    record = {
        "outcome": "failure",
        "source": "mof",
        "finished_at": _utc_now(),
        "run_id": run,
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "run_url": (
            f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
            f"{repo}/actions/runs/{run}" if repo and run else ""
        ),
        "message": "watch-jp failed; inspect the run for the failed stage and publication outcome",
    }
    # The composite action exports actual step outcomes. Do not infer successful
    # retrieval from old state, a previous run's review CSV, or a workflow failure.
    if (os.getenv("MOF_FETCH_OUTCOME") != "success"
            or os.getenv("MOF_PENDING_OUTCOME") != "success"):
        return record
    integrity = os.getenv("MOF_INTEGRITY_OUTCOME")
    approval = os.getenv("MOF_APPROVAL_OUTCOME")
    # Both gates precede commit/push. After they pass, release/notification
    # failure cannot tell us whether publication already happened.
    if integrity != "failure" and not (integrity == "success" and approval == "failure"):
        return record
    try:
        fetched = _candidate_fetch(ROOT, run, record["run_attempt"], record["run_url"])
        record.update(fetch=fetched, stage="publication_error")
        if integrity == "failure":
            record["integrity"] = _blocked_integrity(ROOT, fetched)
            record.update(stage="integrity_blocked",
                          message="MOF fetched and diffed; integrity BLOCKED; master not applied")
        elif os.getenv("MOF_APPROVAL_DECISION") == "review_required":
            record.update(stage="approval_required",
                          message="MOF fetched and diffed; review required; master not applied")
    except (OSError, ValueError, KeyError, TypeError, EOFError, csv.Error) as exc:
        record["classification_error"] = str(exc)[:500]
    return record


def persist(root: Path, record: dict) -> bool:
    if (record.get("outcome") != "failure"
            or record.get("source") != "mof"
            or not record.get("run_id")):
        raise ValueError("invalid MOF failure attempt")
    with tempfile.TemporaryDirectory(prefix="mof-failure-") as folder:
        clean = Path(folder) / "clean"
        created = False
        try:
            for _ in range(3):
                _git(root, "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main")
                if not created:
                    _git(root, "worktree", "add", "--detach", str(clean), "origin/main")
                    created = True
                else:
                    _git(clean, "reset", "--hard", "origin/main")
                destination = clean / FAILURE_PATH
                if destination.exists():
                    previous = json.loads(destination.read_text(encoding="utf-8"))
                    if previous.get("finished_at", "") >= record["finished_at"]:
                        return False
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

                # Rebuild only the presentation file on an untouched main
                # worktree. Candidate master/raw/diffs from the failed run
                # must never be committed by this recovery path.
                D.write_status(clean, [], S.load_state(clean))
                # The failed monitoring candidate is never committed. Only its
                # separately generated, review-only source-transition table is
                # eligible for publication alongside the failure evidence.
                review_file = root / REVIEW_PATH
                pending_file = root / PENDING_PATH
                targets = [FAILURE_PATH.as_posix(), STATUS_PATH.as_posix(),
                           "data/dashboard/status_detail.csv"]
                # Read only the review CSV from the rejected candidate tree.
                # Never copy master, releases, prior approved diffs, or raw
                # files from the failed attempt to the production checkout.
                if pending_file.exists():
                    from .mof_pending_gate import pending_count
                    count = pending_count(root)
                    target = clean / PENDING_PATH
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if count or not target.exists():
                        shutil.copyfile(pending_file, target)
                        targets.append(PENDING_PATH.as_posix())
                if review_file.exists() and _review_case_count(review_file):
                    target = clean / REVIEW_PATH
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(review_file, target)
                    targets.append(REVIEW_PATH.as_posix())
                _git(clean, "add", "--", *targets)
                _git(clean, "-c", "user.name=sanctions-watch[bot]",
                     "-c", "user.email=sanctions-watch@users.noreply.github.com",
                     "commit", "-m", f"chore(monitor): record MOF failure run {record['run_id']}")
                pushed = _git(clean, "push", "origin", "HEAD:refs/heads/main", check=False)
                if pushed.returncode == 0:
                    print(f"MOF failure recorded: {record['run_url']}")
                    return True
            raise RuntimeError("MOF failure-only push rejected after 3 retries")
        finally:
            if created:
                _git(root, "worktree", "remove", "--force", str(clean))
    return False


def main() -> int:
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        print("MOF failure evidence is written only on main")
        return 0
    try:
        persist(ROOT, failure_record())
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"MOF failure evidence persistence failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
