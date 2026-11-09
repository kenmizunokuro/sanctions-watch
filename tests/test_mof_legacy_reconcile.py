import csv
import gzip
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from src import integrity_legacy as L, mof_legacy_reconcile as R, mof_legacy_review as V
from tests.test_mof_legacy_review import ROOT, SIX, SOURCE


class MofLegacyReconcileTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name in (L.LEDGER_PATH, L.SCOPE_PATH, L.POLICY_PATH, 'data/master/master.csv'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, path)
        ledger = json.loads((self.root / L.LEDGER_PATH).read_bytes())
        base = V._read_rows((self.root / 'data/master/master.csv').read_text())
        candidate = base.copy()
        bindings = []
        self.run_url = 'https://github.com/example/repo/actions/runs/123'
        self.checked = '2026-11-07T03:00:00Z'
        for entry in ledger['entries']:
            key = entry['master_key']
            if key in SIX:
                candidate[key] = dict(base[key], sources='OFAC', categories='', last_updated_ms='2000')
                bindings.append(dict(match_key=key, accepted_review_id=entry['accepted_review_id'],
                                     reason='row_changed', changed_fields=['categories', 'last_updated_ms', 'sources']))
        findings = [dict(kind='EXTRA_REGISTRATION', master=candidate[key]) for key in sorted(SIX)]
        findings.append(dict(kind='LEGACY_ACCEPTANCE_INVALID', blocking=True, binding_mismatches=bindings))
        self.report = dict(status='BLOCKED', publication_allowed=False, scope=['mof', 'ofac'],
                           documents=[dict(source='mof', sha256=SOURCE['sha256'], source_url=SOURCE['url'])],
                           inputs={L.LEDGER_PATH: hashlib.sha256((self.root / L.LEDGER_PATH).read_bytes()).hexdigest(),
                                   'data/master/master.csv': 'b' * 64},
                           findings=findings)
        self.report_path = self.root / 'data/integrity_audit/audit.json.gz'
        self.report_path.parent.mkdir(parents=True)
        self.failure = dict(stage='integrity_blocked', source='mof', outcome='failure', run_id='123',
                            run_attempt='1', run_url=self.run_url, finished_at=self.checked,
                            fetch=dict(SOURCE, run_id='123', run_attempt='1', run_url=self.run_url,
                                       checked_at='2026-11-07T02:55:00Z', master_applied=False),
                            integrity={'report': 'data/integrity_audit/audit.json.gz'})
        self.write_report()
        self.rows, _ = V.build_review(base, candidate, ledger, SOURCE, detected_at=self.checked, run_url=self.run_url)
        self.write_review()

    def write_report(self):
        body = gzip.compress(json.dumps(self.report).encode())
        self.report_path.write_bytes(body)
        self.failure['integrity']['report_sha256'] = hashlib.sha256(body).hexdigest()
        path = self.root / 'data/monitoring/mof_failure.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.failure))

    def write_review(self):
        path = self.root / V.OUT
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('w', encoding='utf-8', newline='') as handle:
            csv.writer(handle).writerows([V.COLS, *self.rows])

    def test_links_exact_six_without_writing_business_data(self):
        paths = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = R.reconcile(self.root, self.report_path)
        self.assertEqual(result['status'], 'REVIEW_REQUIRED')
        self.assertEqual(result['run_url'], self.run_url)
        self.assertEqual(result['count'], 6)
        self.assertEqual({c['match_key'] for c in result['cases']}, SIX)
        self.assertEqual({c['case_id'] for c in result['cases']}, {row[0] for row in self.rows})
        self.assertEqual(paths, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_wrong_audit_hash_rejects_report(self):
        self.report_path.write_bytes(self.report_path.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, '監査報告.*SHA256'):
            R.reconcile(self.root, self.report_path)

    def test_published_failure_automatically_writes_verified_artifact(self):
        from src import mof_failure as F
        def git(*args):
            return subprocess.run(['git', '-C', str(self.root), *args], check=True,
                                  capture_output=True, text=True).stdout.strip()
        with tempfile.TemporaryDirectory() as directory:
            origin = Path(directory) / 'origin.git'
            subprocess.run(['git', 'init', '--bare', str(origin)], check=True, capture_output=True)
            (self.root / F.FAILURE_PATH).unlink()
            git('init', '-b', 'main')
            git('config', 'user.name', 'Tests')
            git('config', 'user.email', 'tests@example.invalid')
            git('add', '.')
            git('commit', '-m', 'approved baseline')
            git('remote', 'add', 'origin', str(origin))
            git('push', '-u', 'origin', 'main')
            before = git('show', 'HEAD:data/master/master.csv')
            self.write_report()
            V.write_generated(self.root, self.rows, generated_at=self.checked,
                              run_id='123', run_attempt='1', run_url=self.run_url)
            (self.root / 'data/master/master.csv').write_text('rejected candidate')
            self.assertTrue(F.persist(self.root, self.failure))
            result = json.loads((self.root / R.RECONCILIATION_PATH).read_bytes())
            self.assertEqual(result['status'], 'REVIEW_REQUIRED')
            self.assertEqual(result['count'], 6)
            git('fetch', 'origin', 'main')
            self.assertEqual(git('show', 'origin/main:data/master/master.csv'), before)
            self.assertNotIn(R.RECONCILIATION_PATH.as_posix(), git('ls-tree', '-r', '--name-only', 'origin/main'))

    def _origin(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        origin = Path(directory) / 'origin.git'
        subprocess.run(['git', 'init', '--bare', str(origin)], check=True, capture_output=True)
        def git(*args):
            return subprocess.run(['git', '-C', str(self.root), *args], check=True,
                                  capture_output=True, text=True).stdout.strip()
        (self.root / 'data/monitoring/mof_failure.json').unlink()
        git('init', '-b', 'main')
        git('config', 'user.name', 'Tests')
        git('config', 'user.email', 'tests@example.invalid')
        git('add', '.')
        git('commit', '-m', 'baseline')
        git('remote', 'add', 'origin', str(origin))
        git('push', '-u', 'origin', 'main')
        return origin, git

    def test_publication_retains_first_observation_with_current_audit_bindings(self):
        from src import mof_failure as F
        fresh = [list(row) for row in self.rows]
        for row in self.rows:
            row[1] = '2026-11-01T00:00:00Z'
            row[16] = 'https://github.com/example/repo/actions/runs/100'
            row[13] = 'd' * 64  # a previous poll's bookkeeping hash
        self.write_review()
        _, git = self._origin()
        V.write_generated(self.root, fresh, generated_at=self.checked,
                          run_id='123', run_attempt='1', run_url=self.run_url)
        self.assertTrue(F.persist(self.root, self.failure))
        git('fetch', 'origin', 'main')
        published = git('show', 'origin/main:' + V.OUT.as_posix()) + '\n'
        rows = list(csv.reader(published.splitlines()))[1:]
        self.assertEqual({row[1] for row in rows}, {'2026-11-01T00:00:00Z'})
        self.assertEqual({row[16] for row in rows}, {'https://github.com/example/repo/actions/runs/100'})
        self.assertEqual([row[13] for row in rows], [row[13] for row in fresh])
        result = json.loads((self.root / R.RECONCILIATION_PATH).read_bytes())
        self.assertEqual(result['status'], 'REVIEW_REQUIRED')
        self.assertEqual(result['count'], 6)
        saved = json.loads(git('show', 'origin/main:data/monitoring/mof_failure.json'))
        self.assertEqual(saved['legacy_publication']['sha256'], hashlib.sha256(published.encode()).hexdigest())

    def test_validated_empty_generation_clears_but_stale_csv_is_withheld(self):
        from src import mof_failure as F
        _, git = self._origin()
        before = git('show', 'origin/main:' + V.OUT.as_posix())
        self.rows[0][3] = 'stale file must not be republished'
        self.write_review()
        self.assertTrue(F.persist(self.root, self.failure))
        git('fetch', 'origin', 'main')
        self.assertEqual(git('show', 'origin/main:' + V.OUT.as_posix()), before)
        saved = json.loads(git('show', 'origin/main:data/monitoring/mof_failure.json'))
        self.assertEqual(saved['legacy_publication']['status'], 'WITHHELD')
        self.failure['finished_at'] = '2026-11-07T03:01:00Z'
        V.write_generated(self.root, [], generated_at=self.checked,
                          run_id='123', run_attempt='1', run_url=self.run_url)
        self.assertTrue(F.persist(self.root, self.failure))
        git('fetch', 'origin', 'main')
        self.assertEqual(list(csv.reader(git('show', 'origin/main:' + V.OUT.as_posix()).splitlines())), [V.COLS])

    def test_reconciliation_requires_published_hash_before_accepting_old_observations(self):
        for row in self.rows:
            row[1], row[16] = '2026-11-01T00:00:00Z', 'https://github.com/example/repo/actions/runs/100'
        self.write_review()
        with self.assertRaises(ValueError):
            R.reconcile(self.root, self.report_path)
        self.failure['legacy_publication'] = dict(status='PUBLISHED', count=6,
            generated_at=self.checked,
            sha256=hashlib.sha256((self.root / V.OUT).read_bytes()).hexdigest())
        self.write_report()
        self.assertEqual(R.reconcile(self.root, self.report_path)['count'], 6)
        self.rows[0][16] += '4'
        self.write_review()
        with self.assertRaisesRegex(ValueError, '候補CSV'):
            R.reconcile(self.root, self.report_path)

    def test_generation_receipt_rejects_different_attempt_hash_source_and_future_date(self):
        V.write_generated(self.root, self.rows, generated_at=self.checked,
                          run_id='123', run_attempt='1', run_url=self.run_url)
        self.assertEqual(len(V.read_generated(self.root, self.failure)[0]), 6)
        body = (self.root / V.RECEIPT).read_bytes()
        for field, value in (('run_attempt', '2'), ('sha256', 'e' * 64), ('count', 0),
                             ('generated_at', '2026-11-07T03:01:00Z')):
            with self.subTest(field=field):
                receipt = json.loads(body)
                receipt[field] = value
                (self.root / V.RECEIPT).write_text(json.dumps(receipt))
                with self.assertRaises(ValueError):
                    V.read_generated(self.root, self.failure)
        (self.root / V.RECEIPT).write_bytes(body)
        self.failure['fetch']['sha256'] = 'e' * 64
        with self.assertRaises(ValueError):
            V.read_generated(self.root, self.failure)

    def test_non_object_generation_receipt_is_a_validation_failure(self):
        for value in ([], None, 'not an object'):
            with self.subTest(value=value):
                path = self.root / V.RECEIPT
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    V.read_generated(self.root, self.failure)

    def test_overlap_can_inherit_a_detection_after_this_attempt_started_fetching(self):
        for row in self.rows:
            row[1], row[16] = '2026-11-07T02:57:00Z', 'https://github.com/example/repo/actions/runs/100'
        self.write_review()
        self.failure['legacy_publication'] = dict(status='PUBLISHED', count=6,
            generated_at=self.checked,
            sha256=hashlib.sha256((self.root / V.OUT).read_bytes()).hexdigest())
        self.write_report()
        self.assertEqual(R.reconcile(self.root, self.report_path)['count'], 6)

    def test_concurrent_clear_breaks_observation_continuity_on_push_retry(self):
        from src import mof_failure as F
        fresh = [list(row) for row in self.rows]
        for row in self.rows:
            row[1], row[16] = '2026-11-01T00:00:00Z', 'https://github.com/example/repo/actions/runs/100'
        self.write_review()
        origin, git = self._origin()
        V.write_generated(self.root, fresh, generated_at=self.checked,
                          run_id='123', run_attempt='1', run_url=self.run_url)
        original_git = F._git
        raced = False
        def racing_git(root, *args, **kwargs):
            nonlocal raced
            if args[:1] == ('push',) and not raced:
                raced = True
                rival = origin.parent / 'rival'
                subprocess.run(['git', 'clone', '-b', 'main', str(origin), str(rival)], check=True, capture_output=True)
                with (rival / V.OUT).open('w', newline='') as stream:
                    csv.writer(stream).writerow(V.COLS)
                for command in (['add', '.'], ['-c', 'user.name=Tests', '-c', 'user.email=tests@example.invalid',
                                 'commit', '-m', 'clear candidates'], ['push', 'origin', 'main']):
                    subprocess.run(['git', '-C', str(rival), *command], check=True, capture_output=True)
            return original_git(root, *args, **kwargs)
        with patch.object(F, '_git', side_effect=racing_git):
            self.assertTrue(F.persist(self.root, self.failure))
        git('fetch', 'origin', 'main')
        rows = list(csv.reader(git('show', 'origin/main:' + V.OUT.as_posix()).splitlines()))[1:]
        self.assertTrue(raced)
        self.assertEqual({row[1] for row in rows}, {self.checked})
        self.assertEqual({row[16] for row in rows}, {self.run_url})

    def test_historical_full_row_case_ids_remain_reconcilable(self):
        for row in self.rows:
            row[0] = 'MOF-LEGACY-' + hashlib.sha256('\n'.join((
                row[4], row[12], row[13], row[14])).encode()).hexdigest()[:20]
        self.write_review()
        self.assertEqual(R.reconcile(self.root, self.report_path)['count'], 6)

    def test_failed_reconciliation_replaces_previous_verified_output(self):
        R.write_reconciliation(self.root, self.root, self.failure)
        self.assertEqual(json.loads((self.root / R.RECONCILIATION_PATH).read_bytes())['count'], 6)
        self.rows[0][13] = 'e' * 64
        self.write_review()
        R.write_reconciliation(self.root, self.root, self.failure)
        result = json.loads((self.root / R.RECONCILIATION_PATH).read_bytes())
        self.assertEqual(result['status'], 'REVIEW_UNAVAILABLE')
        self.assertEqual(result['run_url'], self.run_url)
        self.assertNotIn('cases', result)

    def test_stale_or_tampered_review_row_rejects_entire_packet(self):
        for column, value in ((0, 'MOF-LEGACY-forged'), (1, '2026-11-07T03:01:00Z'),
                              (13, 'e' * 64), (16, self.run_url + '4')):
            with self.subTest(column=column):
                original = self.rows[0][column]
                self.rows[0][column] = value
                self.write_review()
                with self.assertRaisesRegex(ValueError, '候補CSV'):
                    R.reconcile(self.root, self.report_path)
                self.rows[0][column] = original
        self.rows.pop()
        self.write_review()
        with self.assertRaisesRegex(ValueError, '候補CSV'):
            R.reconcile(self.root, self.report_path)
