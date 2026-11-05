"""旧Excel・新版master・保存済み一次ソース監査を読み取り専用で比較する。"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import sys
import zlib
from collections import Counter, defaultdict
from pathlib import Path
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from .master import STATUS_ACTIVE, STATUS_INACTIVE
from .normalize import canonical_display_name, is_trailing_unknown_artifact, match_key
from .screening import (
    DECISION_HIT, DECISION_NO_HIT, DECISION_REVIEW, MATCH_EXACT, build_screening_index, screen_name,
)
from .source_evidence import verified_source_inventory


LEGACY_COLUMNS = ("受取人名", "有効なのか")
MASTER_FIELDS = ("display_name", "match_key", "status", "sources", "categories")
MASTER_INPUT = "data/master/master.csv"
STATUSES = (STATUS_ACTIVE, STATUS_INACTIVE)


def _read_legacy(body, sheet_name):
    book = load_workbook(io.BytesIO(body), read_only=True, keep_links=False)
    try:
        sheets = {sheet.title: sheet for sheet in book.worksheets}
        if not sheets or (sheet_name is not None and sheet_name not in sheets):
            raise ValueError(f"旧版Excelに指定シートがありません: {sheet_name}")
        sheet = next(iter(sheets.values())) if sheet_name is None else sheets[sheet_name]
        # The cached sheet dimensions can omit real rows in third-party XLSX files.
        sheet.reset_dimensions()
        rows = sheet.iter_rows()
        header = [cell.value for cell in next(rows, ())]
        if any(header.count(column) != 1 for column in LEGACY_COLUMNS):
            raise ValueError("旧版Excelの受取人名・有効なのか列を一意に確認できません")
        name_column, status_column = (header.index(column) for column in LEGACY_COLUMNS)
        legacy = []
        for number, cells in enumerate(rows, start=2):
            name_cell = cells[name_column] if name_column < len(cells) else None
            status_cell = cells[status_column] if status_column < len(cells) else None
            name = getattr(name_cell, "value", None)
            status = getattr(status_cell, "value", None)
            if name is None and status is None:
                continue
            if (not isinstance(name, str) or name_cell.data_type not in ("s", "inlineStr")
                    or not isinstance(status, str) or status_cell.data_type not in ("s", "inlineStr")
                    or status.strip() not in STATUSES):
                raise ValueError(f"旧版Excelの{number}行目の名称または有効状態が不正です")
            legacy.append({"row": number, "name": name, "status": status.strip()})
        if not legacy:
            raise ValueError("旧版Excelに名簿行がありません")
        return sheet.title, legacy
    finally:
        book.close()


def _read_master(body):
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig"), newline=""), strict=True)
    header = reader.fieldnames or []
    if len(set(header)) != len(header) or not set(MASTER_FIELDS) <= set(header):
        raise ValueError("新版masterのCSV列構造が不正です")
    master = []
    for row in reader:
        if (None in row or any(value is None for value in row.values())
                or row["status"] not in STATUSES or not row["match_key"].strip()
                or not match_key(row["display_name"])):
            raise ValueError(f"新版masterのCSV {reader.line_num}行目が不正です")
        master.append({field: row[field] for field in MASTER_FIELDS})
    if not master:
        raise ValueError("新版masterに名簿行がありません")
    return master


def _official_status(witnesses, complete):
    # Classic Alias CSV has no quality flag. A name from it is weak only if
    # Advanced XML confirms the same name key for the same list and Party.
    if any(not row["weak"] and row["document_role"] != "alias" for row in witnesses):
        return "STRONG_WITNESS"
    if not complete:
        return "INCOMPLETE"
    advanced_parties = {(row["category"], row["source_id"]) for row in witnesses
                        if row["document_role"] == "advanced"}
    if any(row["document_role"] == "alias" and (row["category"], row["source_id"]) not in advanced_parties
           for row in witnesses):
        return "ALIAS_QUALITY_UNDETERMINED"
    return "WEAK_WITNESS_ONLY" if witnesses else "NO_WITNESS_IN_SCOPE"


def _flags(old_rows, old_active, current, official_status):
    decision = current["decision"]
    strong = official_status == "STRONG_WITNESS"
    flags = []
    if not old_active and decision == DECISION_HIT:
        flags.append("NEW_ONLY_HIT")
    if old_active and decision == DECISION_NO_HIT:
        flags.append("LEGACY_ACTIVE_KEY_CURRENT_NO_HIT")
    if old_active and current["match_type"] != MATCH_EXACT:
        flags.append("LEGACY_ACTIVE_KEY_MISSING_FROM_CURRENT_INDEX")
    if decision == DECISION_REVIEW:
        flags.append("CURRENT_REVIEW")
    if strong and not old_active:
        flags.append("OFFICIAL_STRONG_WITHOUT_LEGACY_KEY")
    if strong and decision != DECISION_HIT:
        flags.append("OFFICIAL_STRONG_WITHOUT_CURRENT_HIT")
    if decision == DECISION_HIT and official_status == "WEAK_WITNESS_ONLY":
        flags.append("CURRENT_HIT_WEAK_WITNESS_ONLY")
    if old_active and len(old_active) < len(old_rows):
        flags.append("LEGACY_STATUS_CONFLICT")
    if len(old_active) > 1:
        flags.append("DUPLICATE_LEGACY_ACTIVE_KEY")
    if len(current["candidates"]) > len(current["candidate_keys"]):
        flags.append("DUPLICATE_CURRENT_ACTIVE_KEY")
    return flags


def _compare(legacy, master, inventory):
    old_by_key = defaultdict(list)
    witnesses_by_key = defaultdict(list)
    uncomparable = []
    names = set()
    for row in legacy:
        key = match_key(row["name"])
        if key:
            old_by_key[key].append(row)
            names.add(row["name"])
        else:
            uncomparable.append(row)
    if not old_by_key:
        raise ValueError("旧版Excelに照合キーを生成できる名称がありません")

    for source in inventory["sources"].values():
        for witness in source["evidence"]:
            key = match_key(witness["name"])
            if key:
                witnesses_by_key[key].append(witness)
                names.add(witness["name"])
    coverage = {source: data["status"] for source, data in inventory["sources"].items()}
    complete = all(status == "FOUND" for status in coverage.values())

    current_rows = []
    key_mismatches = []
    for row in master:
        name = canonical_display_name(row["display_name"])
        expected_key = match_key(name)
        if row["match_key"] != expected_key:
            key_mismatches.append(row | {"expected_match_key": expected_key})
        if not is_trailing_unknown_artifact(name):
            current_rows.append(row | {"display_name": name})
            names.add(name)
    index = build_screening_index(current_rows)
    differences = []
    counts = Counter()
    matrix = defaultdict(Counter)
    for name in sorted(names):
        key = match_key(name)
        old_rows = old_by_key.get(key, [])
        old_active = [row for row in old_rows if row["status"] == STATUS_ACTIVE]
        legacy_status = "ACTIVE_KEY" if old_active else ("INACTIVE_KEY" if old_rows else "NO_KEY")
        current = screen_name(name, index)
        matrix[legacy_status][current["decision"]] += 1
        witnesses = witnesses_by_key.get(key, [])
        official_status = _official_status(witnesses, complete)
        flags = _flags(old_rows, old_active, current, official_status)
        if not flags:
            continue
        counts.update(flags)
        differences.append({
            "name": name,
            "match_key": key,
            "legacy": {"status": legacy_status, "rows": old_rows},
            "current": {field: current[field] for field in ("decision", "match_type", "candidates")},
            "official": {"status": official_status, "evidence": witnesses},
            "flags": flags,
        })
    return {
        "audit_status": inventory["audit_status"],
        "official_coverage": coverage,
        "official_source_issues": inventory["source_issues"],
        "summary": {
            "legacy_rows": len(legacy), "master_rows": len(master), "query_names": len(names),
            "decision_matrix": dict(matrix), "flag_counts": dict(counts),
            "current_key_mismatches": len(key_mismatches),
        },
        "legacy_uncomparable": uncomparable,
        "current_key_mismatches": key_mismatches,
        "differences": differences,
    }


def compare_files(legacy_path: Path, master_path: Path, report_path: Path, *, sheet_name=None) -> dict:
    report_body = report_path.read_bytes()
    report = json.loads(gzip.decompress(report_body) if report_body.startswith(b"\x1f\x8b") else report_body)
    master_body = master_path.read_bytes()
    master_sha = hashlib.sha256(master_body).hexdigest()
    inputs = report.get("inputs") if isinstance(report, dict) else None
    if not isinstance(inputs, dict) or inputs.get(MASTER_INPUT) != master_sha:
        raise ValueError("新版masterのSHA256が一次ソース監査レポートの監査対象と一致しません")
    inventory = verified_source_inventory(report)
    master = _read_master(master_body)
    legacy_body = legacy_path.read_bytes()
    sheet, legacy = _read_legacy(legacy_body, sheet_name)
    return {
        "schema_version": 1,
        "legacy_sheet": sheet,
        "input_sha256": {
            "legacy": hashlib.sha256(legacy_body).hexdigest(),
            "master": master_sha,
            "report": hashlib.sha256(report_body).hexdigest(),
        },
    } | _compare(legacy, master, inventory)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy", type=Path, required=True, help="旧版名簿Excel（xlsx）")
    parser.add_argument("--sheet", help="旧版Excelのシート名（省略時は先頭シート）")
    parser.add_argument("--master", type=Path, required=True, help="新版master.csv")
    parser.add_argument("--report", type=Path, required=True, help="新版masterの一次ソース監査JSONまたはgzip")
    args = parser.parse_args(argv)
    try:
        result = compare_files(args.legacy, args.master, args.report, sheet_name=args.sheet)
    except (OSError, EOFError, ValueError, TypeError, KeyError, csv.Error, zlib.error,
            BadZipFile, ParseError, InvalidFileException) as exc:
        print(f"三者比較を実行できませんでした: {exc}", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
