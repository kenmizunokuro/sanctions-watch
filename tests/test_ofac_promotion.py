"""Pending evidence must survive successful publication, separately from apply time."""
from __future__ import annotations

import csv
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src import dashboard as D, master as M, ofac_pending as OP, persistence as P, watch as W

NOW = datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc)
FIRST = "2026-11-07 02:31:25"
FIRST_URL = "https://github.com/test/repo/actions/runs/123"
APPLIED_URL = "https://github.com/test/repo/actions/runs/456"
HISTORY = Path("data/monitoring/ofac_promotion_history.csv")
HISTORY_HEADER = ["検知日時（JST）", "反映日時（UTC）", *OP.DIFF_HEADER,
                  "初回検知実行URL", "反映実行URL"]


class OfacPromotionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rows = {}
        self.diff = M.merge(self.rows, [{"source": "OFAC", "category": "SDN", "name": "LOURDES"}],
                            "OFAC", ts=int(NOW.timestamp() * 1000))
        self.event = M.diff_rows([self.diff])[0]
        self.pending = [OP.HEADER, [FIRST, *self.event, OP.STATE, FIRST_URL]]
        OP.write(self.root / OP.PATH, self.pending)

    def persist(self, diffs=None, now=NOW):
        with patch.dict(os.environ, {"GITHUB_SERVER_URL": "https://github.com",
                                    "GITHUB_REPOSITORY": "test/repo", "GITHUB_RUN_ID": "456"}):
            W._persist_outputs_atomically(root=self.root, rows=self.rows, state={},
                heartbeat=[{"source": "ofac_sdn", "status": "ok"}],
                diffs=[self.diff] if diffs is None else diffs, now=now)

    def read(self, relative):
        with (self.root / relative).open(encoding="utf-8", newline="") as stream:
            return list(csv.reader(stream))

    def snapshot(self):
        return {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_real_promotion_preserves_detection_and_distinct_application_evidence(self):
        self.persist()
        changes = self.read(D.DASH / "changes.csv")
        self.assertEqual(changes, [D.CHANGE_COLS, [FIRST, *self.event]])
        self.assertEqual(self.read(HISTORY), [HISTORY_HEADER,
            [FIRST, "2026-11-09T00:00:00Z", *self.event, FIRST_URL, APPLIED_URL]])
        self.assertEqual(self.read(OP.PATH), self.pending)  # workflow clears only after its gate
        self.assertEqual(M.load(self.root / W.MASTER_REL)["lourdes"]["first_seen_ms"],
                         str(int(NOW.timestamp() * 1000)))

    def test_mixed_exact_new_other_source_and_changed_values(self):
        new = M.Diff("OFAC", added=[{"name": "NEW", "remark": "new"},
                                    {"name": "LOURDES", "remark": "different"}])
        other = M.Diff("財務省", added=[{"name": "LOURDES", "remark": self.event[4]}])
        self.persist([self.diff, new, other])
        changes = self.read(D.DASH / "changes.csv")[1:]
        self.assertEqual(changes[0], [FIRST, *self.event])
        self.assertTrue(all(row[0] == "2026-11-09 09:00:00" for row in changes[1:]))
        self.assertEqual(len(self.read(HISTORY)), 2)

    def test_change_identity_includes_before_and_after(self):
        self.pending = [OP.HEADER, [FIRST, "OFAC", "変更", "LOURDES", "before", "after", OP.STATE, FIRST_URL]]
        OP.write(self.root / OP.PATH, self.pending)
        diffs = [M.Diff("OFAC", changed=[
            {"name": "LOURDES", "before": {"remark": "before"}, "after": {"remark": "different"}},
            {"name": "LOURDES", "before": {"remark": "different"}, "after": {"remark": "after"}},
        ])]
        self.persist(diffs)
        self.assertEqual(self.read(D.DASH / "changes.csv")[1][0], "2026-11-09 09:00:00")
        self.assertFalse((self.root / HISTORY).exists())

    def test_timestamp_lookup_precedes_existing_display_name_cleanup(self):
        raw_event = ["OFAC", "追加", '"Quoted Name"', "", "remark"]
        OP.write(self.root / OP.PATH, [OP.HEADER, [FIRST, *raw_event, OP.STATE, FIRST_URL]])
        self.persist([M.Diff("OFAC", added=[{"name": raw_event[2], "remark": "remark"}])])
        self.assertEqual(self.read(D.DASH / "changes.csv")[1],
                         [FIRST, "OFAC", "追加", "Quoted Name", "", "remark"])
        self.assertEqual(self.read(HISTORY)[1][2:7], raw_event)

    def test_same_display_name_alone_cannot_inherit_detection(self):
        OP.write(self.root / OP.PATH, [OP.HEADER,
            [FIRST, "OFAC", "追加", '"LOURDES"', "", self.event[4], OP.STATE, FIRST_URL]])
        self.persist()
        self.assertEqual(self.read(D.DASH / "changes.csv")[1][0], "2026-11-09 09:00:00")
        self.assertFalse((self.root / HISTORY).exists())

    def test_recurrence_after_clear_does_not_reuse_history_detection(self):
        self.persist()
        OP.write(self.root / OP.PATH, [OP.HEADER])
        before = (self.root / HISTORY).read_bytes()
        self.persist(now=datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(self.read(D.DASH / "changes.csv")[1][0], "2026-11-10 09:00:00")
        self.assertEqual((self.root / HISTORY).read_bytes(), before)

    def test_no_new_promotion_preserves_history_bytes(self):
        self.persist()
        before = (self.root / HISTORY).read_bytes()
        self.persist(diffs=[])
        self.assertEqual((self.root / HISTORY).read_bytes(), before)

    def test_missing_pending_uses_new_detection_without_inventing_history(self):
        (self.root / OP.PATH).unlink()
        self.persist()
        self.assertEqual(self.read(D.DASH / "changes.csv")[1],
                         ["2026-11-09 09:00:00", *self.event])
        self.assertFalse((self.root / HISTORY).exists())

    def test_malformed_duplicate_and_future_pending_fail_before_any_output(self):
        row = self.pending[1]
        variants = [[['wrong header'], row], [OP.HEADER, row, row],
                    [OP.HEADER, ["invalid", *row[1:]]],
                    [OP.HEADER, ["2026-11-10 00:00:00", *row[1:]]],
                    [OP.HEADER, ["2026-11-10 00:00:00", *row[1:3], "UNMATCHED", *row[4:]]],
                    [OP.HEADER, [*row[:7], "wrong-url"]]]
        for pending in variants:
            with self.subTest(pending=pending):
                OP.write(self.root / OP.PATH, pending)
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    self.persist()
                self.assertEqual(self.snapshot(), before)

    def test_existing_history_is_appended_without_rewriting_prior_bytes(self):
        prior = self.root / HISTORY
        OP.write(prior, [HISTORY_HEADER, ["2026-11-01 01:00:00", "2026-11-02T00:00:00Z",
                 "OFAC", "追加", "OLD", "", "old", FIRST_URL, APPLIED_URL]])
        before = prior.read_bytes()
        self.persist()
        self.assertTrue(prior.read_bytes().startswith(before))
        self.assertEqual(len(self.read(HISTORY)), 3)

    def test_replace_failure_rolls_back_history_and_all_formal_outputs(self):
        self.persist()
        before = self.snapshot()
        replace = P.os.replace
        def fail_state(source, target):
            if Path(target) == self.root / W.S.STATE and str(source).endswith(".tmp"):
                raise OSError("injected commit failure")
            return replace(source, target)
        with patch.object(P.os, "replace", side_effect=fail_state):
            with self.assertRaisesRegex(OSError, "injected"):
                self.persist()
        self.assertEqual(self.snapshot(), before)

    def test_failed_first_publication_leaves_no_history_or_partial_generation(self):
        before = self.snapshot()
        replace = P.os.replace
        def fail_state(source, target):
            if Path(target) == self.root / W.S.STATE and str(source).endswith(".tmp"):
                raise OSError("injected first publication failure")
            return replace(source, target)
        with patch.object(P.os, "replace", side_effect=fail_state):
            with self.assertRaisesRegex(OSError, "injected"):
                self.persist()
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.root / HISTORY).exists())


if __name__ == "__main__":
    unittest.main()
