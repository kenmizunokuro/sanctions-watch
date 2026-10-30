"""Only evidence from this fetched candidate can qualify a publication hold."""
import gzip
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from src import mof_failure as F, mof_pending as P
from tests import test_mof_pending as fixtures


class MofFailureEvidenceTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.MofPendingTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root = fixture.root
        self.env = patch.dict(os.environ, {
            "GITHUB_REPOSITORY": "example/repo", "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "2", "MOF_FETCH_OUTCOME": "success",
            "MOF_PENDING_OUTCOME": "success", "MOF_INTEGRITY_OUTCOME": "failure",
            "MOF_APPROVAL_OUTCOME": "skipped",
            "MOF_APPROVAL_DECISION": "",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.patch_root = patch.object(F, "ROOT", self.root)
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)
        self.result = P.run(self.root)
        self.audit = self.root / "data/integrity_audit/20261030__report.json.gz"
        self.audit.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "status": "BLOCKED", "publication_allowed": False,
            "inputs": {"data/master/master.csv": hashlib.sha256(
                (self.root / "data/master/master.csv").read_bytes()).hexdigest()},
            "documents": [{"source": "mof", "sha256": self.result["source_sha256"]}],
            "counts": {"LEGACY_ACCEPTANCE_INVALID": 1},
            "findings": [{"kind": "LEGACY_ACCEPTANCE_INVALID",
                          "detail": "legacy candidate changed", "blocking": True}],
        }
        self.audit.write_bytes(gzip.compress(json.dumps(report).encode()))

    def test_current_validated_candidate_records_audit_hold(self):
        record = F.failure_record()
        self.assertEqual(record.get("stage"), "integrity_blocked")
        self.assertEqual(record["fetch"]["sha256"], self.result["source_sha256"])
        self.assertEqual(record["fetch"]["pending_count"], 3)
        self.assertFalse(record["fetch"]["master_applied"])
        self.assertEqual(record["integrity"]["status"], "BLOCKED")

    def test_previous_attempt_receipt_cannot_hide_current_fetch_failure(self):
        with patch.dict(os.environ, {"GITHUB_RUN_ATTEMPT": "3", "MOF_FETCH_OUTCOME": "failure"}):
            record = F.failure_record()
        self.assertNotIn("fetch", record)
        self.assertNotEqual(record.get("stage"), "integrity_blocked")

    def test_old_run_receipt_cannot_hide_missing_current_evidence(self):
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "124"}):
            record = F.failure_record()
        self.assertNotIn("fetch", record)

    def test_corrupt_fetched_bytes_are_not_a_validated_fetch(self):
        raw = next((self.root / "data/raw/mof").glob('*20261030.csv.gz'))
        raw.write_bytes(gzip.compress(b'corrupt candidate'))
        self.assertNotIn("fetch", F.failure_record())

    def test_audit_crash_is_not_a_review_decision(self):
        self.audit.unlink()
        record = F.failure_record()
        self.assertNotEqual(record.get("stage"), "integrity_blocked")
        self.assertEqual(record.get("stage"), "publication_error")

    def test_valid_fetch_with_passed_integrity_and_pending_approval(self):
        with patch.dict(os.environ, {
            "MOF_INTEGRITY_OUTCOME": "success", "MOF_APPROVAL_OUTCOME": "failure",
            "MOF_APPROVAL_DECISION": "review_required",
        }):
            record = F.failure_record()
        self.assertEqual(record.get("stage"), "approval_required")

    def test_master_only_review_hold_is_valid_with_zero_pending_rows(self):
        P.write_csv(self.root, [])
        path = self.root / 'data/monitoring/mof_candidate.json'
        receipt = json.loads(path.read_text())
        receipt.update(pending_count=0, pending_sha256=hashlib.sha256(
            (self.root / P.PATH).read_bytes()).hexdigest())
        path.write_text(json.dumps(receipt))
        with patch.dict(os.environ, {
            "MOF_INTEGRITY_OUTCOME": "success", "MOF_APPROVAL_OUTCOME": "failure",
            "MOF_APPROVAL_DECISION": "review_required",
        }):
            record = F.failure_record()
        self.assertEqual(record.get("stage"), "approval_required")

    def test_approval_gate_crash_is_not_an_approval_decision(self):
        with patch.dict(os.environ, {
            "MOF_INTEGRITY_OUTCOME": "success", "MOF_APPROVAL_OUTCOME": "failure",
            "MOF_APPROVAL_DECISION": "",
        }):
            record = F.failure_record()
        self.assertEqual(record.get("stage"), "publication_error")

    def test_post_publication_failure_does_not_claim_unapplied_master(self):
        with patch.dict(os.environ, {
            "MOF_INTEGRITY_OUTCOME": "success", "MOF_APPROVAL_OUTCOME": "success",
        }):
            record = F.failure_record()
        self.assertNotIn("fetch", record)
        self.assertNotIn("not published", record["message"])

    def test_receipt_created_only_after_complete_diff(self):
        receipt = self.root / 'data/monitoring/mof_candidate.json'
        self.assertTrue(receipt.exists())
        data = json.loads(receipt.read_text())
        self.assertEqual(data['run_id'], '123')
        self.assertEqual(data['run_attempt'], '2')
        self.assertEqual(data['pending_count'], 3)

    def test_failure_publication_changes_only_monitor_evidence(self):
        record = F.failure_record()
        def git(*args):
            return subprocess.run(['git', '-C', str(self.root), *args],
                                  check=True, capture_output=True, text=True).stdout.strip()
        with tempfile.TemporaryDirectory() as folder:
            origin = Path(folder) / 'origin.git'
            subprocess.run(['git', 'init', '--bare', str(origin)], check=True,
                           capture_output=True)
            git('init', '-b', 'main')
            git('config', 'user.email', 'tests@example.invalid')
            git('config', 'user.name', 'Tests')
            git('add', '.')
            git('commit', '-m', 'applied baseline')
            git('remote', 'add', 'origin', str(origin))
            git('push', '-u', 'origin', 'main')
            before = git('rev-parse', 'HEAD')
            self.assertTrue(F.persist(self.root, record))
            git('fetch', 'origin', 'main')
            changed = set(git('diff', '--name-only', before, 'origin/main').splitlines())
            self.assertEqual(changed, {
                'data/monitoring/mof_failure.json', 'data/dashboard/status.csv',
                'data/dashboard/status_detail.csv',
            })
            detail = git('show', 'origin/main:data/dashboard/status_detail.csv')
            legacy = git('show', 'origin/main:data/dashboard/status.csv')
            self.assertIn('取得済・監査停止（master未反映）', detail)
            self.assertIn('財務省,エラー,', legacy)
            self.assertEqual(git('show', 'origin/main:data/master/master.csv'),
                             'unchanged master sentinel')


if __name__ == '__main__':
    unittest.main()
