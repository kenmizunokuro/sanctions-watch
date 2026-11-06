"""Link a published MOF legacy review CSV to its blocked audit, without writing data."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import sys
import zlib
from datetime import datetime
from pathlib import Path

from . import integrity_legacy as L, mof_legacy_review as R
from .mof_failure import FAILURE_PATH


def reconcile(root: Path, report_path: Path) -> dict:
    failure = json.loads((root / FAILURE_PATH).read_bytes())
    fetch = failure.get("fetch", {})
    run_url = failure.get("run_url", "")
    if (failure.get("source") != "mof" or failure.get("stage") != "integrity_blocked"
            or failure.get("outcome") != "failure" or fetch.get("master_applied") is not False
            or not re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+/actions/runs/[0-9]+", run_url)
            or any(not failure.get(key) or failure[key] != fetch.get(key)
                   for key in ("run_id", "run_attempt", "run_url"))
            or run_url.rsplit("/", 1)[-1] != failure["run_id"]):
        raise ValueError("財務省の同一実行に結び付く監査停止証跡がありません")
    compressed = report_path.read_bytes()
    report_hash = hashlib.sha256(compressed).hexdigest()
    if report_hash != failure["integrity"]["report_sha256"]:
        raise ValueError("監査報告のSHA256が公開済みの失敗証跡と一致しません")
    report = json.loads(gzip.decompress(compressed))
    if (report.get("status") != "BLOCKED" or report.get("publication_allowed") is not False
            or "mof" not in report.get("scope", [])
            or not any(doc.get("source") == "mof" and doc.get("sha256") == fetch["sha256"]
                       and doc.get("source_url") == fetch["url"] for doc in report["documents"])):
        raise ValueError("監査報告と財務省原本の証跡が一致しません")

    ledger_body = (root / L.LEDGER_PATH).read_bytes()
    if hashlib.sha256(ledger_body).hexdigest() != report["inputs"].get(L.LEDGER_PATH):
        raise ValueError("旧登録の承認台帳が監査時の版と一致しません")
    ledger = json.loads(ledger_body)
    finished_at = datetime.fromisoformat(failure["finished_at"])
    checked_at = datetime.fromisoformat(fetch["checked_at"])
    accepted = L._validate(ledger, L.load_scope(root), finished_at)
    bindings = [row for finding in report["findings"]
                if finding["kind"] == "LEGACY_ACCEPTANCE_INVALID" and finding.get("blocking") is True
                for row in finding.get("binding_mismatches", [])]
    by_key = {R._text(row["match_key"]): row for row in bindings}
    if (not bindings or len(by_key) != len(bindings)
            or any(row["reason"] != "row_changed" for row in bindings)):
        raise ValueError("監査報告に照合可能な旧登録の変更証跡がそろっていません")

    with (root / R.OUT).open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, strict=True)
        if next(reader, []) != R.COLS:
            raise ValueError("旧登録の候補CSVの列が一致しません")
        rows = [dict(zip(R.COLS, row, strict=True)) for row in reader]
    if len(rows) != len(by_key) or {row["match_key"] for row in rows} != by_key.keys():
        raise ValueError("旧登録の候補CSVと監査報告の対象行が一致しません")

    base = R._read_rows((root / "data/master/master.csv").read_text(encoding="utf-8"))
    keys = {row["match_key"] for row in bindings}
    audited_rows = {}
    for finding in report["findings"]:
        candidate = finding.get("master") or {}
        if candidate.get("match_key") in keys:
            audited_rows[R._hash(candidate)] = candidate

    cases = []
    for row in rows:
        binding = by_key[row["match_key"]]
        key, review_id = binding["match_key"], binding["accepted_review_id"]
        if not checked_at <= datetime.fromisoformat(row["検知日時UTC"]) <= finished_at:
            raise ValueError(f"旧登録の候補CSVの検知日時が実行範囲外です: {key}")
        approval = accepted.get(review_id)
        prior = base.get(key)
        current = audited_rows.get(row["候補master SHA256"])
        if (approval is None or approval["master_key"] != key or prior is None
                or R._hash(prior) != approval["accepted_master_sha256"]
                or current is None or current["match_key"] != key):
            raise ValueError(f"旧登録の候補CSVを承認台帳・監査対象行と照合できません: {key}")
        expected = R.review_case(prior, current, fetch, detected_at=row["検知日時UTC"], run_url=run_url)
        changed_fields = sorted(field for field in prior.keys() | current.keys()
                                if field not in prior or field not in current or prior[field] != current[field])
        if expected != list(row.values()) or changed_fields != binding["changed_fields"]:
            raise ValueError(f"旧登録の候補CSVと監査報告の変更内容が一致しません: {key}")
        cases.append(dict(case_id=row["候補ID"], match_key=key, accepted_review_id=review_id,
                          approved_row_sha256=row["旧master SHA256"], candidate_row_sha256=row["候補master SHA256"],
                          changed_fields=changed_fields, display_name=current["display_name"],
                          before={field: prior[field] for field in ("sources", "categories", "status")},
                          after={field: current[field] for field in ("sources", "categories", "status")}))

    return dict(status="REVIEW_REQUIRED", run_url=run_url, run_attempt=failure["run_attempt"],
                official_source_url=fetch["url"], official_source_sha256=fetch["sha256"],
                audit_report_sha256=report_hash, candidate_master_sha256=report["inputs"]["data/master/master.csv"],
                acceptance_expires_at=ledger["expires_at"], count=len(cases),
                cases=sorted(cases, key=lambda case: case["match_key"]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=R.ROOT)
    parser.add_argument("--report", required=True, type=Path, help="展開済み監査レポートの .json.gz ファイル")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(reconcile(args.root, args.report), ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, TypeError, EOFError, csv.Error, zlib.error) as error:
        print(f"MOF LEGACY REVIEW BLOCKED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
