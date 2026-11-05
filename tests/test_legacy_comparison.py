import csv
import gzip
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from openpyxl import Workbook

from src.legacy_comparison import compare_files
from src.normalize import match_key
from tests.test_source_evidence import MOF_DOC, MOF_NAME, ofac_report


class LegacyComparisonTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.legacy = Path(directory.name) / "old.xlsx"
        self.master = Path(directory.name) / "master.csv"
        self.report = Path(directory.name) / "audit.json.gz"

    def workbook(self, rows):
        workbook = Workbook()
        workbook.active.append(["受取人名", "有効なのか"])
        for row in rows:
            workbook.active.append(row)
        workbook.save(self.legacy)

    def inputs(self, master, witnesses, *, complete=True):
        fields = ("display_name", "match_key", "status", "sources", "categories")
        with self.master.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(fields)
            writer.writerows((name, match_key(name), status, "財務省", "") for name, status in master)
        lineage = [MOF_NAME | {"name": name, "master_key": match_key(name), "weak": weak}
                   for name, weak in witnesses]
        report = ofac_report()
        report["scope"].append("mof")
        report["documents"].append(MOF_DOC | {"name_count": len(lineage) + int(not complete)})
        report["lineage"].extend(lineage)
        report["inputs"] = {"data/master/master.csv": hashlib.sha256(self.master.read_bytes()).hexdigest()}
        self.report.write_bytes(gzip.compress(json.dumps(report).encode()))

    def test_classifies_old_and_new_differences_and_preserves_raw_legacy_rows(self):
        self.workbook([("Legacy Only", "有効"), ("Legacy Only", "有効"),
                       ("Weak Alias", "無効"), ("別名A／別名B", "有効")])
        self.inputs([("New Supported", "有効"), ("New Unsourced", "有効"), ("Weak Alias", "有効"),
                     ("Missing Strong", "無効")],
                    [("New Supported", False), ("Weak Alias", True), ("Missing Strong", False),
                     ("別名B", False)])
        result = compare_files(self.legacy, self.master, self.report)
        rows = {row["name"]: row for row in result["differences"]}
        self.assertEqual(rows["New Supported"]["official"]["status"], "STRONG_WITNESS")
        self.assertIn("NEW_ONLY_HIT", rows["New Supported"]["flags"])
        self.assertEqual(rows["New Unsourced"]["official"]["status"], "NO_WITNESS_IN_SCOPE")
        self.assertEqual(rows["Weak Alias"]["official"]["status"], "WEAK_WITNESS_ONLY")
        self.assertEqual(rows["Weak Alias"]["legacy"]["status"], "INACTIVE_KEY")
        self.assertEqual([r["row"] for r in rows["Legacy Only"]["legacy"]["rows"]], [2, 3])
        self.assertIn("DUPLICATE_LEGACY_ACTIVE_KEY", rows["Legacy Only"]["flags"])
        self.assertIn("OFFICIAL_STRONG_WITHOUT_CURRENT_HIT", rows["Missing Strong"]["flags"])
        self.assertEqual(rows["別名B"]["legacy"]["status"], "NO_KEY")

    def test_incomplete_coverage_cannot_report_no_or_weak_only_witness(self):
        self.workbook([("Legacy Only", "有効")])
        self.inputs([("New Name", "有効")], [("Other Name", False)], complete=False)
        result = compare_files(self.legacy, self.master, self.report)
        row = next(r for r in result["differences"] if r["name"] == "New Name")
        self.assertEqual(result["official_coverage"]["mof"], "INCOMPLETE")
        self.assertEqual(row["official"]["status"], "INCOMPLETE")

    def test_unknown_document_role_cannot_supply_strong_witness(self):
        self.workbook([("Legacy", "有効")])
        self.inputs([("New Name", "有効")], [("New Name", False)])
        report = json.loads(gzip.decompress(self.report.read_bytes()))
        report["documents"][-1]["role"] = "unknown"
        self.report.write_bytes(gzip.compress(json.dumps(report).encode()))
        rows = {row["name"]: row for row in compare_files(self.legacy, self.master, self.report)["differences"]}
        self.assertEqual(rows["New Name"]["official"], {"status": "INCOMPLETE", "evidence": []})

    def test_current_relaxed_collision_is_review(self):
        self.workbook([("AB", "有効")])
        self.inputs([("A(B)", "有効"), ("(A)B", "有効")], [("AB", False)])
        result = compare_files(self.legacy, self.master, self.report)
        row = next(r for r in result["differences"] if r["name"] == "AB")
        self.assertEqual(row["current"]["decision"], "REVIEW")
        self.assertIn("CURRENT_REVIEW", row["flags"])
        self.assertNotIn("NEW_ONLY_HIT", row["flags"])

    def test_current_relaxed_hit_is_distinct_from_exact_key_retention(self):
        self.workbook([("AB", "有効")])
        self.inputs([("A(B)", "有効")], [("AB", False)])
        result = compare_files(self.legacy, self.master, self.report)
        row = next(r for r in result["differences"] if r["name"] == "AB")
        self.assertEqual(row["current"]["decision"], "HIT")
        self.assertIn("LEGACY_ACTIVE_KEY_MISSING_FROM_CURRENT_INDEX", row["flags"])
        self.assertNotIn("LEGACY_ACTIVE_KEY_CURRENT_NO_HIT", row["flags"])

    def test_stale_excel_dimensions_cannot_hide_legacy_rows(self):
        self.workbook([("First Name", "有効"), ("Last Name", "有効")])
        original = io.BytesIO(self.legacy.read_bytes())
        with ZipFile(original) as source, ZipFile(self.legacy, "w") as target:
            for path in source.namelist():
                body = source.read(path)
                if path == "xl/worksheets/sheet1.xml":
                    self.assertIn(b'<dimension ref="A1:B3"', body)
                    body = body.replace(b'<dimension ref="A1:B3"', b'<dimension ref="A1:B2"')
                target.writestr(path, body)
        self.inputs([("Last Name", "有効")], [("Last Name", False)])
        result = compare_files(self.legacy, self.master, self.report)
        self.assertEqual(result["summary"]["legacy_rows"], 2)
        self.assertEqual(result["summary"]["decision_matrix"]["ACTIVE_KEY"]["HIT"], 1)

    def test_shared_weak_hit_and_conflicting_legacy_states_are_reported(self):
        self.workbook([("Weak Alias", "有効"), ("Weak Alias", "無効")])
        self.inputs([("Weak Alias", "有効")], [("Weak Alias", True)])
        result = compare_files(self.legacy, self.master, self.report)
        row = next(r for r in result["differences"] if r["name"] == "Weak Alias")
        self.assertEqual(row["official"]["status"], "WEAK_WITNESS_ONLY")
        self.assertIn("CURRENT_HIT_WEAK_WITNESS_ONLY", row["flags"])
        self.assertIn("LEGACY_STATUS_CONFLICT", row["flags"])

    def test_classic_ofac_alias_does_not_upgrade_weak_or_unclassified_evidence(self):
        self.workbook([("Legacy", "有効")])
        self.inputs([("WEAK EXAMPLE", "有効"), ("Classic Only", "有効")], [("Other", False)])
        report = json.loads(gzip.decompress(self.report.read_bytes()))
        alias = report["lineage"][1]
        advanced = report["lineage"][2]
        alias.update(name="EXAMPLE, Weak", master_key=match_key("EXAMPLE, Weak"))
        advanced.update(name="WEAK EXAMPLE", master_key=match_key("WEAK EXAMPLE"), weak=True)
        report["lineage"].append(alias | {"name": "Classic Only", "master_key": match_key("Classic Only")})
        report["documents"][1]["name_count"] = 2
        self.report.write_bytes(gzip.compress(json.dumps(report).encode()))
        differences = {row["name"]: row for row in compare_files(self.legacy, self.master, self.report)["differences"]}
        weak = differences["WEAK EXAMPLE"]["official"]
        self.assertEqual(weak["status"], "WEAK_WITNESS_ONLY")
        self.assertEqual({item["document_role"] for item in weak["evidence"]}, {"advanced", "alias"})
        self.assertEqual(differences["Classic Only"]["official"]["status"], "ALIAS_QUALITY_UNDETERMINED")

    def test_master_hash_mismatch_fails_without_report_output(self):
        self.workbook([("Legacy", "有効")])
        self.inputs([("New", "有効")], [("New", False)])
        with self.master.open("ab") as stream:
            stream.write(b"\n")
        command = [sys.executable, "-m", "src.legacy_comparison", "--legacy", str(self.legacy),
                   "--master", str(self.master), "--report", str(self.report)]
        failed = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("SHA256", failed.stderr)

    def test_inconsistent_current_key_is_reported_even_if_relaxed_search_hits(self):
        self.workbook([("New Name", "有効")])
        self.inputs([("New Name", "有効")], [("New Name", False)])
        self.master.write_bytes(self.master.read_bytes().replace(b",newname,", b",wrongkey,"))
        report = json.loads(gzip.decompress(self.report.read_bytes()))
        report["inputs"]["data/master/master.csv"] = hashlib.sha256(self.master.read_bytes()).hexdigest()
        self.report.write_bytes(gzip.compress(json.dumps(report).encode()))
        result = compare_files(self.legacy, self.master, self.report)
        mismatch, = result["current_key_mismatches"]
        self.assertEqual(mismatch["match_key"], "wrongkey")
        self.assertEqual(mismatch["expected_match_key"], "newname")
        self.assertEqual(result["summary"]["decision_matrix"]["ACTIVE_KEY"]["HIT"], 1)

    def test_unknown_legacy_status_is_rejected(self):
        self.workbook([("Legacy", "要確認")])
        self.inputs([("New", "有効")], [("New", False)])
        with self.assertRaisesRegex(ValueError, "2行目"):
            compare_files(self.legacy, self.master, self.report)
