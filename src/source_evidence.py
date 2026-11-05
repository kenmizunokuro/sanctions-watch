"""保存済みの一次ソース監査レポートから、指定名称の原本証跡を検索する。"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
import zlib
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from .normalize import match_key
from .source_audit import safe_audit_url


DOCUMENTS = {
    "mof": {("mof", "mof")},
    "ofac": {
        (source, role)
        for source in ("ofac_sdn", "ofac_cons")
        for role in ("primary", "alias", "advanced")
    },
}
OFAC_LISTS = {"SDN": "ofac_sdn", "Consolidated": "ofac_cons"}
EVIDENCE_FIELDS = (
    "source", "category", "source_id", "un_id", "name", "primary", "weak",
    "source_url", "source_hash", "raw_path", "locator", "master_key", "master_rows", "id_link",
)
FINDING_FIELDS = (
    "kind", "detail", "blocking", "checked_source", "legacy_accepted", "legacy_expires_at",
    "uncertainty_status", "legacy_review_id", "retention_approved", "retention_review_id",
)
MASTER_FIELDS = ("display_name", "match_key", "status", "sources", "categories")
OFFICIAL_FIELDS = ("source", "category", "source_id", "un_id", "name", "locator")
INDEX_FIELDS = ("list", "party_id", "name", "alias_current", "party_current")


def _select(row, fields):
    return {key: row[key] for key in fields if key in row}


def _objects(value, field):
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"監査レポートの {field} が不正です")
    return value


def _report_rows(report):
    if not isinstance(report, dict) or report.get("schema_version") != 1:
        raise ValueError("対応する一次ソース監査レポートは schema_version=1 です")
    scope = report.get("scope")
    if (not isinstance(scope, list) or not scope
            or any(not isinstance(source, str) or source not in DOCUMENTS for source in scope)):
        raise ValueError("監査レポートの scope が不正です")
    if report.get("status") not in ("PASS", "REVIEW", "BLOCKED"):
        raise ValueError("監査レポートの status が不正です")
    return tuple(_objects(report.get(field), field) for field in ("documents", "lineage", "findings"))


def _document_key(row, source, hash_field):
    url = row.get("source_url")
    digest = row.get(hash_field)
    path = row.get("raw_path")
    if not all(isinstance(value, str) and value for value in (url, digest, path)):
        return None
    if not re.fullmatch("[0-9a-f]{64}", digest) or not path.startswith("data/raw/"):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
    except ValueError:
        return None
    domains = ("mof.go.jp",) if source == "mof" else ("treas.gov", "treasury.gov")
    if (parts.scheme != "https" or parts.username is not None
            or not any(host == domain or host.endswith("." + domain) for domain in domains)):
        return None
    return (source, path, digest, url)


def _has_witness_fields(row):
    required = ("name", "category", "source_id", "locator", "master_key", "id_link")
    return (
        all(isinstance(row.get(field), str) and row[field].strip() for field in required)
        and type(row.get("master_rows")) is int and row["master_rows"] >= 0
        and all(isinstance(row.get(field), bool) for field in ("primary", "weak"))
    )


def _finding_summary(finding, key):
    master = finding.get("master", [])
    if isinstance(master, dict):
        master = [master]
    rows = [row for row in _objects(master, "finding.master")
            if row.get("match_key") == key or match_key(row.get("display_name", "")) == key]
    official = finding.get("official", {})
    index = finding.get("index", [])
    if isinstance(index, dict):
        index = [index]
    if not isinstance(official, dict):
        raise ValueError("監査レポートの finding.official が不正です")
    index = _objects(index, "finding.index")
    if rows or match_key(official.get("name", "")) == key or any(
        row.get("match_key") == key or match_key(row.get("name", "")) == key for row in index
    ):
        return _select(finding, FINDING_FIELDS) | {
            "master": [_select(row, MASTER_FIELDS) for row in rows],
            "official": _select(official, OFFICIAL_FIELDS),
            "index": [_select(row, INDEX_FIELDS) for row in index],
        }
    return None


def lookup(report: dict, name: str) -> dict:
    """現在原本との対応をレポート内で確認し、掲載状態と監査指摘を分ける。"""
    key = match_key(name)
    if not key:
        raise ValueError("照合キーが空です")
    documents, lineage, findings = _report_rows(report)
    source_issues = [
        finding for finding in findings
        if finding.get("kind") == "UPSTREAM_FAILED"
        or (finding.get("kind") == "UNDETERMINED"
            and not any(field in finding for field in ("master", "official", "index")))
    ]

    counted = Counter()
    matches = {}
    for row in lineage:
        label = row.get("source")
        source = "mof" if label == "財務省" else None
        if label == "OFAC":
            source = OFAC_LISTS.get(row.get("category"))
        if not source:
            raise ValueError("監査レポートの lineage に未対応の出所があります")
        document_key = _document_key(row, source, "source_hash") if _has_witness_fields(row) else None
        counted[source, document_key] += 1
        if document_key and (row["master_key"] == key or match_key(row["name"]) == key):
            witness = _select(row, EVIDENCE_FIELDS)
            witness["source_url"] = safe_audit_url(witness["source_url"])
            if row["master_key"] != key:
                witness["match_basis"] = "CURRENT_NAME_KEY"
            matches.setdefault(document_key, []).append(witness)

    sources = {}
    for source, expected in DOCUMENTS.items():
        if source not in report["scope"]:
            sources[source] = {"status": "OUT_OF_SCOPE", "evidence": []}
            continue
        source_ids = {item[0] for item in expected}
        relevant = [doc for doc in documents if doc.get("source") in source_ids]
        roles = {(doc.get("source"), doc.get("role")) for doc in relevant}
        incomplete = (
            roles != expected
            or len(relevant) != len(expected)
            or any(issue.get("source") in source_ids | {None} for issue in source_issues)
        )
        matched = []
        observed_count = 0
        for doc in relevant:
            document_key = _document_key(doc, doc["source"], "sha256")
            count = counted[doc["source"], document_key]
            if document_key is None or not count or count != doc.get("name_count"):
                incomplete = True
                continue
            observed_count += count
            matched.extend(matches.get(document_key, ()))
        if observed_count != sum(count for (src, _), count in counted.items() if src in source_ids):
            incomplete = True
        status = "INCOMPLETE" if incomplete else ("FOUND" if matched else "NO_WITNESS")
        sources[source] = {"status": status, "evidence": matched}

    return {
        "query": name,
        "match_key": key,
        "audit_status": report["status"],
        "sources": sources,
        "findings": [item for finding in findings if (item := _finding_summary(finding, key)) is not None],
        "source_issues": [_select(issue, ("kind", "source", "detail")) for issue in source_issues],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="一次ソース監査レポート（JSONまたはgzip）")
    parser.add_argument("--name", required=True, help="既存の照合キーで検索する名称")
    args = parser.parse_args(argv)
    try:
        payload = args.report.read_bytes()
        body = gzip.decompress(payload) if payload.startswith(b"\x1f\x8b") else payload
        result = lookup(json.loads(body), args.name)
    except (OSError, EOFError, ValueError, TypeError, zlib.error) as exc:
        print(f"証跡を検索できませんでした: {exc}", file=sys.stderr)
        return 2
    result["report_sha256"] = hashlib.sha256(payload).hexdigest()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
