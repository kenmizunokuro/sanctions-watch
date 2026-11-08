"""OFAC取得実行の成否を業務データと別のファイルへ記録する。"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ATTEMPT_PATH = Path("data/monitoring/ofac_attempt.json")
INTEGRITY_FAILED = "integrity_failed"


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_attempt(root: Path, command: list[str], sources: list[str]) -> int:
    started_at = timestamp()
    try:
        code = subprocess.run(command, cwd=root, check=False).returncode
    except OSError as error:
        print(f"OFAC取得プロセスを開始できません: {error}", file=sys.stderr)
        code = 1
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    record = {
        "started_at": started_at,
        "finished_at": timestamp(),
        "outcome": "success" if code == 0 else "failure",
        "exit_code": code,
        "sources": sources,
        "run_id": run_id,
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "run_url": f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else "",
    }
    _write(root / ATTEMPT_PATH, record)
    return code


def _write(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
            staged = Path(file.name)
            file.write(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        os.replace(staged, path)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def mark_integrity_failure(root: Path, *, run_id: str, run_attempt: str,
                           audit_report: str = '', audit_report_sha256: str = '') -> bool:
    path = root / ATTEMPT_PATH
    if not run_id or not run_attempt or not path.exists():
        return False
    record = json.loads(path.read_text(encoding="utf-8"))
    if (record.get("run_id") != run_id or record.get("run_attempt") != run_attempt
            or record.get("outcome") != "success" or record.get("exit_code") != 0
            or not {"ofac", "all"}.intersection(record.get("sources", []))):
        return False
    from . import ofac_integrity
    try:
        integrity = ofac_integrity.capture(root, audit_report, audit_report_sha256)
    except (OSError, ValueError, TypeError, KeyError, EOFError, zlib.error) as exc:
        integrity = dict(status='UNAVAILABLE', reason=str(exc)[:500])
    _write(path, record | {
        "outcome": "failure",
        "exit_code": 1,
        "stage": INTEGRITY_FAILED,
        "fetch_finished_at": record["finished_at"],
        "finished_at": timestamp(),
        "integrity": integrity,
    })
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--sources", nargs="+", choices=["ofac", "mof", "all"])
    mode.add_argument("--mark-integrity-failure", action="store_true")
    args = parser.parse_args()
    if args.mark_integrity_failure:
        if mark_integrity_failure(ROOT, run_id=os.environ.get("GITHUB_RUN_ID", ""),
                                  run_attempt=os.environ.get("GITHUB_RUN_ATTEMPT", ""),
                                  audit_report=os.environ.get('OFAC_AUDIT_REPORT', ''),
                                  audit_report_sha256=os.environ.get('OFAC_AUDIT_REPORT_SHA256', '')):
            return 0
        print("OFAC監査停止に対応する同一実行の取得成功記録がありません", file=sys.stderr)
        return 1
    if "ofac" not in args.sources and "all" not in args.sources:
        parser.error("OFACを含む取得だけに使用してください")
    return run_attempt(ROOT, [sys.executable, "-m", "src.watch", "--sources", *args.sources], args.sources)


if __name__ == "__main__":
    sys.exit(main())
