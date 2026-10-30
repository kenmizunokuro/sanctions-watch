"""MOF pending source diffs are review-only and reproducible."""
from __future__ import annotations

import csv
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import mof_pending as P, mof_pending_gate as G, mof_record_diff as R
from tests.test_mof_record_diff import make_row, write_raw


class MofPendingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.before = write_raw(
            self.root, "20261020T120000Z__shisantouketsu20261021.csv.gz",
            [make_row(), make_row(**{"番号": "002-000799",
                                      "氏名（英語）": "REMOVED OFFICIAL",
                                      "国連参照番号": "QDi.399"})])
        self.after = write_raw(
            self.root, "20261030T030000Z__shisantouketsu20261030.csv.gz",
            [make_row(**{"旅券番号": "Z999999"}), make_row(**{
                "番号": "002-000800", "氏名（英語）": "=HYPERLINK(\"evil\")",
                "氏名（日本語）": "", "国連参照番号": "QDi.450"})])
        state = self.root / "data/source_record_state/mof.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"processed_raw": str(self.before.relative_to(self.root))}))
        official = {
            "mof": {
                "sha256": R._parse_csv(self.after).content_hash,
                "url": "https://www.mof.go.jp/test/shisantouketsu20261030.csv",
            }
        }
        (self.root / "data/state.json").write_text(json.dumps(official))
        master = self.root / "data/master/master.csv"
        master.parent.mkdir(parents=True, exist_ok=True)
        master.write_text("unchanged master sentinel")

    def test_all_types_are_listed_no_master_or_source_state_mutation(self):
        master = (self.root / "data/master/master.csv").read_bytes()
        state = (self.root / "data/source_record_state/mof.json").read_bytes()
        result = P.run(self.root)
        self.assertEqual(result["state"], "PENDING_REVIEW")
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["removed"], 1)
        self.assertEqual(result["amended_records"], 1)
        with (self.root / P.PATH).open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 3)
        self.assertEqual({row["種別"] for row in rows},
                         {"情報改訂", "原本レコード追加", "原本レコード削除"})
        self.assertEqual({row["検証状態"] for row in rows}, {P.STATUS})
        self.assertEqual(len({row["候補ID"] for row in rows}), len(rows))
        self.assertEqual({row["今回原本SHA256"] for row in rows},
                         {result["source_sha256"]})
        added = next(row for row in rows if row["種別"] == "原本レコード追加")
        self.assertTrue(added["対象者"].startswith("'="))
        self.assertEqual((self.root / "data/master/master.csv").read_bytes(), master)
        self.assertEqual((self.root / "data/source_record_state/mof.json").read_bytes(), state)
        self.assertEqual(G.pending_count(self.root), 3)
        again = P.run(self.root)
        self.assertEqual(again["candidates"], 3)
        with (self.root / P.PATH).open(encoding="utf-8", newline="") as handle:
            second = list(csv.DictReader(handle))
        self.assertEqual({r["候補ID"] for r in rows}, {r["候補ID"] for r in second})

    def test_missing_or_mismatched_source_fails_closed(self):
        state = self.root / "data/state.json"
        original = json.loads(state.read_text())
        state.write_text(json.dumps({"mof": {**original["mof"], "sha256": "0" * 64}}))
        with self.assertRaisesRegex(R.RecordDiffError, "SHA256"):
            P.run(self.root)

    def test_no_raw_must_not_be_called_no_changes(self):
        (self.root / "data/raw/mof").rename(self.root / "data/raw/mof-old")
        with self.assertRaisesRegex(R.RecordDiffError, "raw data missing"):
            P.run(self.root)

    def test_review_gate_blocks_pending_even_if_master_is_unchanged(self):
        P.run(self.root)
        with patch.object(G.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
            self.assertEqual(G.gate(self.root), 3)
        with patch.object(G.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
            self.assertEqual(G.gate(self.root), 3)

    def test_master_only_hold_exports_review_decision(self):
        P.write_csv(self.root, [])
        output = self.root / 'step-output.txt'
        with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), patch.object(
                G.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
            self.assertEqual(G.gate(self.root), 3)
        self.assertEqual(output.read_text(), "decision=review_required\n")

    def test_review_gate_fails_on_schema_change(self):
        csv_file = self.root / P.PATH
        csv_file.parent.mkdir(parents=True, exist_ok=True)
        csv_file.write_text("invalid,header\n")
        with self.assertRaisesRegex(ValueError, "columns changed"):
            G.pending_count(self.root)

    def test_distinct_source_sha_changes_case_identity(self):
        P.run(self.root)
        with (self.root / P.PATH).open(encoding="utf-8", newline="") as file:
            ids1 = {r["候補ID"] for r in csv.DictReader(file)}
        self.assertTrue(ids1)
        # Case identity must incorporate both official snapshot hashes.
        key = P._case_id("a" * 64, "b" * 64, "情報改訂", "1", "旅券番号", "x", "y")
        changed = P._case_id("a" * 64, "c" * 64, "情報改訂", "1", "旅券番号", "x", "y")
        self.assertNotEqual(key, changed)


if __name__ == "__main__":
    unittest.main()
