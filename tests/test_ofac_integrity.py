import gzip
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import monitor_attempt


class OfacIntegrityDetailsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = {}
        for name in ('data/master/master.csv', 'data/state.json', 'data/master/ofac_alias_history.csv'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'candidate bytes\n')
            self.inputs[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.report = dict(schema_version=1, scope=['ofac', 'mof'], status='BLOCKED', publication_allowed=False,
            inputs=self.inputs, findings=[
                dict(kind='EXTRA_REGISTRATION', blocking=True, detail='No current source',
                     checked_source='OFAC', master={'display_name': 'CARLOTA C'}),
                dict(kind='WEAK_ALIAS_ACTIVE', blocking=False, master={'display_name': 'IGNORED'})])
        self.path = self.root / 'data/integrity_audit/report.json.gz'
        self.path.parent.mkdir(parents=True)

    def capture(self, *, digest=None, path=None):
        self.path.write_bytes(gzip.compress(json.dumps(self.report).encode()))
        env = dict(GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='2', GITHUB_REPOSITORY='test/repo')
        with patch.dict(os.environ, env):
            monitor_attempt.run_attempt(self.root, [__import__('sys').executable, '-c', 'pass'], ['ofac'])
        monitor_attempt.mark_integrity_failure(self.root, run_id='123', run_attempt='2',
            audit_report=path or 'data/integrity_audit/report.json.gz',
            audit_report_sha256=digest or hashlib.sha256(self.path.read_bytes()).hexdigest())
        return json.loads((self.root / monitor_attempt.ATTEMPT_PATH).read_text())

    def test_captures_only_blockers_from_the_exact_candidate_report(self):
        record = self.capture()
        self.assertEqual(record['outcome'], 'failure')
        details = record['integrity']
        self.assertEqual(details['status'], 'BLOCKED')
        self.assertEqual(details['blocking_counts'], {'EXTRA_REGISTRATION': 1})
        self.assertEqual(details['blockers'][0]['name'], 'CARLOTA C')
        self.assertEqual(len(details['blockers']), 1)
        self.assertEqual(details['report_sha256'], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(details['report'], 'data/integrity_audit/report.json.gz')

    def test_stale_hash_or_changed_candidate_keeps_failure_with_unavailable_reasons(self):
        mutations = ['report_hash', *self.inputs, 'scope', 'pass', 'path', 'empty', 'corrupt']
        original = json.loads(json.dumps(self.report))
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.report = json.loads(json.dumps(original))
                if mutation in self.inputs:
                    self.report['inputs'][mutation] = '0' * 64
                if mutation == 'scope': self.report['scope'] = ['mof']
                if mutation == 'pass': self.report['publication_allowed'] = True
                if mutation == 'empty': self.report['findings'] = []
                if mutation == 'corrupt': self.report['findings'] = None
                record = self.capture(digest='0' * 64 if mutation == 'report_hash' else None,
                                      path='../outside.json.gz' if mutation == 'path' else None)
                self.assertEqual(record['stage'], 'integrity_failed')
                self.assertEqual(record['integrity']['status'], 'UNAVAILABLE')
                self.assertNotIn('blocking_counts', record['integrity'])

    def test_audit_crash_without_report_does_not_invent_zero_blockers(self):
        env = dict(GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='2', GITHUB_REPOSITORY='test/repo')
        with patch.dict(os.environ, env):
            monitor_attempt.run_attempt(self.root, [__import__('sys').executable, '-c', 'pass'], ['ofac'])
        self.assertTrue(monitor_attempt.mark_integrity_failure(self.root, run_id='123', run_attempt='2'))
        record = json.loads((self.root / monitor_attempt.ATTEMPT_PATH).read_text())
        self.assertEqual(record['integrity']['status'], 'UNAVAILABLE')
        self.assertTrue(record['integrity']['reason'])


if __name__ == '__main__':
    unittest.main()
