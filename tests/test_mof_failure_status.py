"""MOF failure evidence is visible but does not replace last verified source data."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src import dashboard as D
from src import state as S


class MofFailureDashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.st = {
            "mof": {
                "asof": "令和8年10月21日",
                "record_count": 15284,
                "sha256": "f8e1038419ab",
            }
        }
        S.heartbeat(self.root, [
            {"source": "mof", "status": "unchanged", "content_hash": "f8e1038419ab",
             "source_updated": "令和8年10月21日", "record_count": "15284"},
        ], now=datetime(2026,10,29,6,22,45,tzinfo=timezone.utc))

    def _failure(self, finished_at):
        p = self.root / "data/monitoring/mof_failure.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "source": "mof", "outcome": "failure", "finished_at": finished_at,
            "run_id": "123", "run_url": "https://github.com/example/r/actions/runs/123",
        }), encoding="utf-8")

    def _mof(self):
        return next(r for r in D.build_status_rows(
            self.root, [], self.st,
            now=datetime(2026,10,30,1,tzinfo=timezone.utc),
        ) if r[0] == "財務省")

    def test_failed_attempt_is_not_misreported_as_unchanged(self):
        self._failure("2026-10-29T23:01:00Z")
        source, status, checked, asof, count, checksum = self._mof()
        self.assertEqual(status, "エラー")
        self.assertEqual(checked, "2026-10-30 08:01:00")
        self.assertEqual(asof, "令和8年10月21日")
        self.assertEqual(count, "15284")
        self.assertEqual(checksum, "f8e1038419ab")

    def test_subsequent_success_removes_failure_overlay(self):
        self._failure("2026-10-29T23:01:00Z")
        S.heartbeat(self.root, [
            {"source": "mof", "status": "unchanged", "content_hash": "newsha",
             "source_updated": "令和8年10月30日", "record_count": "15281"},
        ], now=datetime(2026,10,30,1,2,tzinfo=timezone.utc))
        self.assertEqual(self._mof()[1:3], ["変更なし", "2026-10-30 10:02:00"])

    def test_corrupted_failure_evidence_is_not_silently_ignored(self):
        p = self.root / "data/monitoring/mof_failure.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"outcome": "success"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            self._mof()


if __name__ == "__main__":
    unittest.main()
