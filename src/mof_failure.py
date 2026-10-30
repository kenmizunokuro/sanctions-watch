"""Persist an unsuccessful MOF attempt without publishing unverified business data.

Run only after a failed watch-jp job. This is failure evidence, never a list
update, delisting decision or replacement for the integrity gate.
"""
from __future__ import annotations

import csv
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


def failure_record() -> dict:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run = os.environ.get("GITHUB_RUN_ID", "")
    return {
        "outcome": "failure",
        "source": "mof",
        "finished_at": _utc_now(),
        "run_id": run,
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "run_url": (
            f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
            f"{repo}/actions/runs/{run}" if repo and run else ""
        ),
        "message": "watch-jp failed; retrieved candidate and integrity result were not published",
    }


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
                rows = D.build_status_rows(clean, [], S.load_state(clean))
                D.write_status_rows(clean / STATUS_PATH, rows)
                # The failed monitoring candidate is never committed. Only its
                # separately generated, review-only source-transition table is
                # eligible for publication alongside the failure evidence.
                review_file = root / REVIEW_PATH
                targets = [FAILURE_PATH.as_posix(), STATUS_PATH.as_posix()]
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
