"""Regress the real six accepted legacy MOF aliases lost by the Oct 30 candidate."""
from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from src import mof_legacy_review as R
from src import integrity_legacy as L

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


def historical_fixture():
    """Pin pre-delist review cases to the immutable approved legacy scope."""
    base = R._read_rows((ROOT / 'data/master/master.csv').read_text(encoding='utf-8'))
    ledger = json.loads((ROOT / L.LEDGER_PATH).read_bytes())
    scope = L.load_scope(ROOT)
    ledger['entries'] = [L._entry(ident, item, ledger['expires_at']) for ident, item in scope.items()]
    for item in scope.values():
        base[item['master_key']] = L.annotate(item['finding']['master'], item['classification'], ledger['expires_at'])
    return base, ledger


class MofLegacyReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base, cls.ledger = historical_fixture()

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

    def test_repeat_monitor_timestamp_does_not_create_a_new_case(self):
        key = sorted(SIX)[0]
        before = self.base[key]
        current = dict(before, sources="OFAC", categories="", last_updated_ms="1000")
        first = R.review_case(before, current, SOURCE, detected_at="2026-11-01T00:00:00Z", run_url="first")
        current["last_updated_ms"] = "2000"
        repeated = R.review_case(before, current, SOURCE, detected_at="2026-11-02T00:00:00Z", run_url="second")
        self.assertEqual(first[0], repeated[0])
        self.assertNotEqual(first[13], repeated[13])  # exact current audit binding still changes
        current["risk_level"] = "changed"
        changed = R.review_case(before, current, SOURCE, detected_at="2026-11-02T00:00:00Z", run_url="second")
        self.assertNotEqual(first[0], changed[0])

    def test_first_detection_survives_repeat_but_not_clear_or_material_change(self):
        before = self.base[sorted(SIX)[0]]
        current = dict(before, sources="OFAC", categories="", last_updated_ms="1000")
        first = R.review_case(before, current, SOURCE, detected_at="2026-11-01T00:00:00Z",
                              run_url="https://github.com/example/repo/actions/runs/100")
        current["last_updated_ms"] = "2000"
        latest = R.review_case(before, current, SOURCE, detected_at="2026-11-02T00:00:00Z",
                               run_url="https://github.com/example/repo/actions/runs/200")
        saved = R.retain_first_detection([latest], [first])[0]
        self.assertEqual((saved[1], saved[16]), (first[1], first[16]))
        self.assertEqual(saved[13], latest[13])
        self.assertEqual(R.retain_first_detection([latest], []), [latest])
        current["status"] = "無効"
        changed = R.review_case(before, current, SOURCE, detected_at=latest[1], run_url=latest[16])
        self.assertEqual(R.retain_first_detection([changed], [first]), [changed])
        with self.assertRaises(ValueError):
            R.retain_first_detection([first], [latest])
        with self.assertRaises(ValueError):
            R.retain_first_detection([latest], [first, first])
        foreign = list(first)
        foreign[16] = 'https://github.com/foreign/repo/actions/runs/100'
        with self.assertRaises(ValueError):
            R.retain_first_detection([latest], [foreign])

    def test_old_case_ids_migrate_only_when_all_changed_values_are_visible(self):
        before = self.base[sorted(SIX)[0]]
        current = dict(before, sources="OFAC", categories="", last_updated_ms="1000")
        old = R.review_case(before, current, SOURCE, detected_at="2026-11-01T00:00:00Z",
                            run_url="https://github.com/example/repo/actions/runs/100")
        old[0] = 'MOF-LEGACY-' + hashlib.sha256('\n'.join((
            old[4], old[12], old[13], old[14])).encode()).hexdigest()[:20]
        current['last_updated_ms'] = '2000'
        latest = R.review_case(before, current, SOURCE, detected_at="2026-11-02T00:00:00Z",
                               run_url="https://github.com/example/repo/actions/runs/200")
        merged = R.retain_first_detection([latest], [old])[0]
        self.assertEqual((merged[0], merged[1]), (latest[0], old[1]))
        changed = list(old)
        changed[5] += ';risk_level'
        self.assertEqual(R.retain_first_detection([latest], [changed]), [latest])
        changed = list(old)
        changed[12] = 'f' * 64
        self.assertEqual(R.retain_first_detection([latest], [changed]), [latest])

    def test_acceptance_and_source_evidence_must_be_valid(self):
        for ledger, source in [
            ({**self.ledger, "state": "DRAFT"}, SOURCE),
            ({**self.ledger, "entries": self.ledger["entries"] + [self.ledger['entries'][0]]}, SOURCE),
            ({**self.ledger, "entries": self.ledger['entries'][:-1]}, SOURCE),
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
