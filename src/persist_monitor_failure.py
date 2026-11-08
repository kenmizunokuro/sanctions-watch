"""OFAC失敗状態をクリーンなmainから保存する。"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from . import dashboard, ofac_pending, ofac_integrity, state
from .monitor_attempt import ATTEMPT_PATH, INTEGRITY_FAILED, ROOT


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], check=check, capture_output=True, text=True)


def _finished(record: dict) -> datetime:
    return datetime.strptime(record["finished_at"], "%Y-%m-%dT%H:%M:%SZ")


def persist_failure(root: Path, *, run_id: str, run_attempt: str | None = None,
                    candidate_sha256: str = "", base_sha256: str = "") -> bool:
    path = root / ATTEMPT_PATH
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    record = json.loads(text)
    if not run_id or record.get("run_id") != run_id or record.get("outcome") != "failure":
        return False
    if run_attempt is not None and (not run_attempt or record.get("run_attempt") != run_attempt):
        return False
    if not ({"ofac", "all"} & set(record.get("sources", []))):
        return False
    finished = _finished(record)
    preview = None
    if record.get("stage") == INTEGRITY_FAILED:
        try:
            preview = ([ofac_pending.HEADER] if candidate_sha256 == "none" else ofac_pending.build(
                (root / ofac_pending.DIFF_PATH).read_bytes(), candidate_sha256,
                record["fetch_finished_at"], record["run_url"],
            ))
        except (OSError, ValueError, csv.Error) as exc:
            print(f"OFACレビュー専用差分を保存できません: {exc}", file=sys.stderr)
    with tempfile.TemporaryDirectory(prefix="ofac-monitor-") as directory:
        clean = Path(directory) / "clean"
        created = False
        try:
            for _ in range(3):
                _git(root, "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main")
                if not created:
                    _git(root, "worktree", "add", "--detach", str(clean), "origin/main")
                    created = True
                else:
                    # Only this disposable worktree is reset. Original staged data stays untouched.
                    _git(clean, "reset", "--hard", "origin/main")
                destination = clean / ATTEMPT_PATH
                if destination.exists():
                    previous = json.loads(destination.read_text(encoding="utf-8"))
                    if _finished(previous) >= finished:
                        return False
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(text, encoding="utf-8")
                dashboard.write_status(clean, [], state.load_state(clean))
                paths = [ATTEMPT_PATH.as_posix(), "data/dashboard/status.csv", "data/dashboard/status_detail.csv"]
                if record.get('stage') == INTEGRITY_FAILED:
                    ofac_integrity.write_summary(clean, record)
                    paths.append(ofac_integrity.PATH.as_posix())
                if preview is not None:
                    if hashlib.sha256((clean / "data/master/master.csv").read_bytes()).hexdigest() == base_sha256:
                        ofac_pending.write(clean / ofac_pending.PATH, preview)
                        paths.append(ofac_pending.PATH.as_posix())
                    else:
                        print("OFAC差分候補の比較元masterが更新済みのため、次回取得で再計算します", file=sys.stderr)
                _git(clean, "add", "--", *paths)
                _git(clean, "-c", "user.name=sanctions-watch[bot]", "-c", "user.email=sanctions-watch@users.noreply.github.com", "commit", "-m", f"chore(monitor): OFAC監視失敗 run {run_id}")
                pushed = _git(clean, "push", "origin", "HEAD:refs/heads/main", check=False)
                if pushed.returncode == 0:
                    print("OFAC失敗状態のみをmainへ保存しました")
                    return True
            raise RuntimeError("OFAC失敗状態のpushに3回失敗しました")
        finally:
            if created:
                _git(root, "worktree", "remove", "--force", str(clean))
    return False


def main() -> int:
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        print("main以外では失敗状態の保存をスキップします")
        return 0
    persisted = persist_failure(ROOT, run_id=os.environ.get("GITHUB_RUN_ID", ""),
                                run_attempt=os.environ.get("GITHUB_RUN_ATTEMPT", ""),
                                candidate_sha256=os.environ.get("OFAC_CANDIDATE_SHA256", ""),
                                base_sha256=os.environ.get("OFAC_BASE_SHA256", ""))
    if not persisted:
        print("今回のOFAC失敗状態は未作成、またはより新しい状態が保存済みです")
    return 0


if __name__ == "__main__":
    sys.exit(main())
