"""Block publication of unapproved MOF source changes.

A validated fetch/diff is not authorization to alter the screening master.
The nonempty pending-review CSV is published through the dedicated
failure/evidence path, rather than the ordinary run-watch commit path.
"""
from __future__ import annotations

import csv
import subprocess
from pathlib import Path

from .mof_pending import COLUMNS, PATH, STATUS


def pending_count(root: Path) -> int:
    path = root / PATH
    if not path.exists():
        raise ValueError("MOF pre-approval evidence CSV is missing")
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, strict=True)
        if reader.fieldnames != COLUMNS:
            raise ValueError("MOF pre-approval candidate columns changed")
        rows = list(reader)
    if any(None in r or None in r.values() or not r["候補ID"].startswith("MOF-PENDING-")
           or r["検証状態"] != STATUS for r in rows):
        raise ValueError("MOF pre-approval candidates invalid")
    if len({r["候補ID"] for r in rows}) != len(rows):
        raise ValueError("MOF pre-approval duplicate case IDs")
    return len(rows)


def gate(root: Path) -> int:
    count = pending_count(root)
    diff = subprocess.run(
        ["git", "-C", str(root), "diff", "--quiet", "HEAD", "--",
         "data/master/master.csv"],
        check=False,
    )
    if diff.returncode not in {0, 1}:
        raise RuntimeError("cannot compare candidate master against published main")
    if count or diff.returncode == 1:
        print(f"MOF REVIEW REQUIRED: {count} source-change candidates; "
              f"master_modified={diff.returncode == 1}; "
              "candidate CSV may be published, master is BLOCKED")
        return 3
    print("MOF pre-approval check: no master or official record changes")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(gate(Path(__file__).resolve().parent.parent))
    except (ValueError, OSError, RuntimeError, csv.Error) as error:
        print(f"MOF APPROVAL GATE BLOCKED: {error}")
        raise SystemExit(2)
