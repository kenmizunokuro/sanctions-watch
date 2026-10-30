"""MOF delist review report keeps actual source changes blocked."""
import copy
import unittest
from unittest.mock import patch

from src import integrity_legacy as L, mof_legacy_review as R
from tests.test_integrity_legacy import scope_fixture, sample, active, REVIEWED, EXPIRES


class CandidateReviewTests(unittest.TestCase):
    def setUp(self):
        self.item = sample()
        self.scope = scope_fixture()
        self.rows, draft = L.prepare(
            [self.item["finding"]["master"]], self.scope, REVIEWED, EXPIRES)
        self.ledger = active(draft)
        self.original = self.rows[0]

    def test_identical_candidate_needs_no_review(self):
        with patch.object(L, "_validate", return_value={
            self.ledger["entries"][0]["accepted_review_id"]:
            self.ledger["entries"][0],
        }):
            self.assertEqual(R.review_rows(
                {self.original["match_key"]: copy.deepcopy(self.original)},
                self.scope, self.ledger), [])

    def test_source_change_is_exported_without_approval(self):
        candidate = copy.deepcopy(self.original)
        candidate["sources"] = "財務省"
        candidate["categories"] = ""
        candidate["last_updated_ms"] = "2000"
        with patch.object(L, "_validate", return_value={
            self.ledger["entries"][0]["accepted_review_id"]:
            self.ledger["entries"][0],
        }):
            report = R.review_rows({candidate["match_key"]: candidate},
                                   self.scope, self.ledger)
        self.assertEqual(len(report), 1)
        self.assertIn("sources", report[0]["change_fields"])
        self.assertIn("categories", report[0]["change_fields"])
        self.assertEqual(report[0]["decision"], "REVIEW_REQUIRED_NOT_APPLIED")
        self.assertEqual(candidate["sources"], "OFAC")
        self.assertEqual(self.original["sources"],
                         self.item["finding"]["master"]["sources"])

    def test_missing_approved_row_is_not_silently_ignored(self):
        with patch.object(L, "_validate", return_value={
            self.ledger["entries"][0]["accepted_review_id"]:
            self.ledger["entries"][0],
        }):
            report = R.review_rows({}, self.scope, self.ledger)
        self.assertEqual(report[0]["decision"], "REVIEW_REQUIRED_NOT_APPLIED")
        self.assertIn("match_key", report[0]["change_fields"])


if __name__ == "__main__":
    unittest.main()
