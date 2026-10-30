"""Regress the real six accepted legacy MOF aliases lost by the Oct 30 candidate."""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from src import mof_legacy_review as R

ROOT = Path(__file__).resolve().parent.parent
SOURCE = {
    "sha256": "c" * 64,
    "url": "https://www.mof.go.jp/policy/international_policy/gaitame_kawase/"
           "gaitame/economic_sanctions/list.html",
}
SIX = {
    "abual-darda", "bassamahmadal-hasri", "abuahmadal-shami",
    "abuahmadakhlaq", "bassamahmadhusari", "iyadnazmisalihkhalil",
}


class MofLegacyReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = R._read_rows(
            (ROOT / "data/master/master.csv").read_text(encoding="utf-8"))
        cls.ledger = json.loads(
            (ROOT / "data/review/integrity_legacy_acceptances.json")
            .read_text(encoding="utf-8"))

    def test_review_only_six_mof_source_relations_with_no_automatic_delist(self):
        self.assertEqual(len(SIX), 6)
        self.assertTrue(SIX <= self.base.keys())
        candidate = copy.deepcopy(self.base)
        for key in SIX:
            before = self.base[key]
            self.assertIn("財務省", before["sources"])
            self.assertIn("LEGACY_UNVERIFIED_V1:", before["review_flag"])
            candidate[key]["sources"] = "OFAC"
            candidate[key]["categories"] = ""
            candidate[key]["last_updated_ms"] = "2000"
        rows, counts = R.build_review(
            self.base, candidate, self.ledger, SOURCE,
            detected_at="2026-10-30T09:00:00Z", run_url="https://github.com/run/1")
        self.assertEqual(counts["pending_review"], 6)
        self.assertEqual(counts["approved_legacy_count"], 2429)
        self.assertEqual({r[4] for r in rows}, SIX)
        self.assertEqual({r[2] for r in rows}, {"REVIEW_REQUIRED"})
        self.assertEqual({r[7] for r in rows}, {"OFAC"})
        self.assertTrue(all(r[10] == r[11] == "有効" for r in rows))
        # This function must never touch either source of truth.
        self.assertIn("財務省", self.base["bassamahmadal-hasri"]["sources"])
        self.assertEqual(candidate["bassamahmadal-hasri"]["status"], "有効")

    def test_legacy_warning_only_change_is_not_incorrect_delist(self):
        candidate = dict(self.base)
        key = "bassamahmadal-hasri"
        candidate[key] = dict(self.base[key])
        candidate[key]["remark"] = "legacy warning erroneously changed"
        candidate[key]["last_updated_ms"] = "2000"
        rows, result = R.build_review(
            self.base, candidate, self.ledger, SOURCE,
            detected_at="2026-10-30T09:00:00Z")
        self.assertEqual(rows, [])
        self.assertEqual(result["annotation_only_changes"], 1)

    def test_acceptance_and_source_evidence_must_be_valid(self):
        for ledger, source in [
            ({**self.ledger, "state": "DRAFT"}, SOURCE),
            ({**self.ledger, "entries": self.ledger["entries"][:-1]}, SOURCE),
            (self.ledger, {"sha256": "", "url": SOURCE["url"]}),
        ]:
            with self.subTest(state=ledger.get("state"), sha=source.get("sha256")):
                with self.assertRaises(ValueError):
                    R.build_review(self.base, self.base, ledger, source,
                                   detected_at="2026-10-30T09:00:00Z")

    def test_formula_escape_and_review_csv_verification(self):
        self.assertEqual(R._text("=HYPERLINK(\"evil\")")[:1], "'")
        from src.mof_failure import _review_case_count
        import csv
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "review.csv"
            with p.open("w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(R.COLS)
                w.writerow(["MOF-LEGACY-123", "2026-10-30T09:00:00Z",
                            "REVIEW_REQUIRED"] + ["value"] * (len(R.COLS) - 3))
            self.assertEqual(_review_case_count(p), 1)
            p.write_text("invalid,columns\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                _review_case_count(p)


if __name__ == "__main__":
    unittest.main()
