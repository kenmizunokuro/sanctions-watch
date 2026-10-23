"""Git-level regressions for the read-only historical evidence companion."""
import csv
import gzip
import hashlib
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'docs/evidence/reproduce-integrity-history-20261024.py'
spec = importlib.util.spec_from_file_location('history_evidence', SCRIPT)
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)


class HistoryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.email', 'fixture@example.test')
        self.git('config', 'user.name', 'History fixture')
        self.patch = patch.object(H, 'ROOT', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], stderr=subprocess.DEVNULL).decode().strip()

    def write(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def commit(self, message):
        self.git('add', '.')
        self.git('commit', '-q', '--allow-empty', '-m', message)
        return self.git('rev-parse', 'HEAD')

    def alias(self, name):
        stream = io.StringIO()
        csv.writer(stream).writerow(['22504', '1', 'a.k.a.', name, '-0-'])
        return stream.getvalue().encode()

    def test_merge_resolution_raw_body_is_inspected(self):
        self.commit('initial')
        self.git('branch', 'other')
        self.write('left.txt', b'left')
        self.commit('left')
        self.git('checkout', '-q', 'other')
        self.write('right.txt', b'right')
        self.commit('right')
        self.git('checkout', '-q', 'main')
        self.git('merge', '--no-commit', '--no-ff', 'other')
        # This raw body first exists in the merge, absent from both parents.
        self.write('data/raw/ofac_sdn/fixture__alt.csv', self.alias('EXAMPLE NAME'))
        merge = self.commit('raw added during merge resolution')
        with patch.object(H, 'BASE', merge):
            documents, witnesses = H.archived_names({H.match_key('EXAMPLE NAME')})
        self.assertTrue(witnesses.get(H.match_key('EXAMPLE NAME')))
        self.assertEqual(documents[0]['status'], 'parsed')

    def test_bad_stored_key_does_not_hide_archived_display_name(self):
        display = 'CHERNOMORSKI BANK, OTKRYTOE OBSHCHESTVO'
        correct = H.match_key(display)
        wrong = correct + "''"
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=['match_key', 'display_name', 'sources'])
        writer.writeheader()
        writer.writerow(dict(match_key=wrong, display_name=display, sources='OFAC'))
        master = stream.getvalue().encode()
        self.write(H.MASTER, master)
        self.write('data/raw/ofac_sdn/fixture__alt.csv', self.alias(display))
        base = self.commit('initial data')
        triage = dict(items=[dict(review_id='fixture', classification='KEY_COLLISION_REVIEW',
                                 master_key=wrong, display_name=display, index_links=[])],
                      counts={'KEY_COLLISION_REVIEW': 1}, archive_manifest=[],
                      source_audit_inputs={H.MASTER: hashlib.sha256(master).hexdigest()})
        body = gzip.compress(json.dumps(triage).encode(), mtime=0)
        self.write(H.TRIAGE, body)
        with patch.object(H, 'BASE', base), patch.object(H, 'TRIAGE_HASH', hashlib.sha256(body).hexdigest()):
            report = H.build_report()
        record = report['records'][0]
        self.assertTrue(record['archived_name_witnesses'])
        self.assertEqual(record['archived_name_witnesses'][0]['record']['source_id'], '22504')
        self.assertEqual(record['master_key'], wrong)
        self.assertEqual(record['first_exact_stored_key_observation']['records'][0]['row']['match_key'], wrong)


class RecoveryBaselineTests(unittest.TestCase):
    def setUp(self):
        recovery = SCRIPT.with_name('reproduce-integrity-recovery-20261024.py')
        module_spec = importlib.util.spec_from_file_location('recovery_evidence', recovery)
        self.module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(self.module)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for folder in ('ofac_sdn', 'ofac_cons', 'ofac_sdn_advanced', 'ofac_cons_advanced', 'mof'):
            (self.root / 'data/raw' / folder).mkdir(parents=True)
        notice = self.root / 'docs/evidence/integrity-notices'
        notice.mkdir(parents=True)
        (notice / 'manifest.json').write_text('[]')
        previous = dict(source_audit_inputs={}, items=[], counts={}, archive_manifest=[])
        triage = self.root / 'previous.json.gz'
        triage.write_bytes(gzip.compress(json.dumps(previous).encode(), mtime=0))
        investigation = dict(items=[], archive_errors=[], archive_manifest=[], approved_count=0, counts={}, retention_candidates=0)
        audit = dict(status='BLOCKED', publication_allowed=False, counts={}, inputs={})
        for patcher in (
            patch.object(self.module, 'ROOT', self.root),
            patch.object(self.module, 'TRIAGE', triage),
            patch.object(self.module, 'PINNED_TRIAGE_HASH', hashlib.sha256(triage.read_bytes()).hexdigest()),
            patch.object(self.module, 'RECOVER', []),
            patch.object(self.module.A, 'audit', return_value=audit),
            patch.object(self.module.T, 'triage', return_value=investigation),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_unchanged_empty_baseline_is_accepted(self):
        self.assertEqual(self.module.run()['changed_classification_count'], 0)

    def test_unexpected_historical_body_rejects_baseline(self):
        (self.root / 'data/raw/ofac_sdn/fourth__alt.csv').write_text('22504,1,a.k.a.,EXTRA NAME,-0-\n')
        with self.assertRaisesRegex(ValueError, 'baseline'):
            self.module.run()


if __name__ == '__main__':
    unittest.main()
