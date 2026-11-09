import copy
import csv
import gzip
import hashlib
import io
import json
import shutil
import subprocess
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src import approved_legacy_delist as A, integrity_legacy as L
from src import mof_legacy_review as V

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 9, 2, tzinfo=timezone.utc)
SIX = {'abuahmadakhlaq', 'abuahmadal-shami', 'abual-darda',
       'bassamahmadal-hasri', 'bassamahmadhusari', 'iyadnazmisalihkhalil'}


class ApprovedLegacyDelistTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for rel in (A.APPROVAL, L.LEDGER_PATH, L.POLICY_PATH, L.SCOPE_PATH,
                    'data/master/master.csv', 'data/dashboard/list.csv',
                    'data/dashboard/screening.csv.gz', 'data/dashboard/changes.csv',
                    'data/diff/latest.csv', V.OUT, 'data/review/ofac_party_removal_queue.csv'):
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / rel, target)
        self.approval = json.loads((self.root / A.APPROVAL).read_bytes())
        # Keep the approved historical before state even after production applies it.
        rows = V._read_rows((self.root / 'data/master/master.csv').read_text())
        ledger = json.loads((self.root / L.LEDGER_PATH).read_bytes())
        for case in self.approval['cases']:
            rows[case['match_key']] = case['before']
            if not any(e['master_key'] == case['match_key'] for e in ledger['entries']):
                ledger['entries'].append(case['withdrawn_acceptance'])
        from src import master as M, dashboard as D
        M.save(rows, self.root / 'data/master/master.csv')
        (self.root / L.LEDGER_PATH).write_text(json.dumps(ledger))
        D.write_list(self.root, rows)
        D.write_screening_gzip(self.root, rows)

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file()}

    def test_only_approved_six_are_deactivated_and_history_screening_stay_consistent(self):
        before = V._read_rows((self.root / 'data/master/master.csv').read_text())
        unrelated = V.review_case(before['abufaris'], dict(before['abufaris'], categories='review only'),
            {'sha256': 'a' * 64, 'url': 'https://www.mof.go.jp/list.csv'},
            detected_at='2026-11-09T01:00:00Z', run_url='https://github.com/example/repo/actions/runs/100')
        with (self.root / V.OUT).open('a', newline='', encoding='utf-8') as stream:
            csv.writer(stream).writerow(unrelated)
        old_history = list(csv.reader(io.StringIO((self.root / 'data/dashboard/changes.csv').read_text())))[1:]
        receipt = A.apply(self.root, now=NOW, run_url='https://github.com/example/repo/actions/runs/123')
        after = V._read_rows((self.root / 'data/master/master.csv').read_text())
        self.assertEqual({k for k in before if before[k] != after[k]}, SIX)
        self.assertEqual({k for k in SIX if after[k]['status'] == '無効'}, SIX)
        self.assertTrue(all(after[k]['first_seen_ms'] == before[k]['first_seen_ms'] for k in SIX))
        ledger = json.loads((self.root / L.LEDGER_PATH).read_bytes())
        self.assertEqual(len(ledger['entries']), 2423)
        self.assertFalse(SIX & {e['master_key'] for e in ledger['entries']})
        screened = list(csv.DictReader(io.StringIO(gzip.decompress(
            (self.root / 'data/dashboard/screening.csv.gz').read_bytes()).decode())))
        self.assertFalse(SIX & {r['match_key'] for r in screened})
        history = list(csv.reader(io.StringIO((self.root / 'data/dashboard/changes.csv').read_text())))[1:]
        events = [r for r in history if r[0] == '2026-11-09 11:00:00']
        self.assertEqual(len(events), 6)
        self.assertEqual({r[2] for r in events}, {'掲載終了'})
        self.assertTrue(all(r in history for r in old_history[:100]))
        self.assertFalse(SIX & {r[4] for r in V.read_csv(self.root)})
        self.assertIn(unrelated, V.read_csv(self.root))
        self.assertEqual(receipt['count'], 6)
        self.assertEqual(receipt['master_after_sha256'], hashlib.sha256(
            (self.root / 'data/master/master.csv').read_bytes()).hexdigest())
        L.validate_for_export(self.root, list(after.values()), now=NOW)
        L._display(self.root, list(after.values()))
        review, totals = V.build_review(after, after, ledger,
            {'sha256': 'a' * 64, 'url': 'https://www.mof.go.jp/list.csv'},
            detected_at='2026-11-09T02:00:00Z')
        self.assertEqual(review, [])
        self.assertEqual(totals['approved_legacy_count'], 2423)
        saved = self.snapshot()
        self.assertEqual(A.apply(self.root, now=NOW)['status'], 'ALREADY_APPLIED')
        self.assertEqual(saved, self.snapshot())

    def test_changed_selected_row_or_approval_blocks_without_any_writes(self):
        path = self.root / 'data/master/master.csv'
        rows = V._read_rows(path.read_text())
        rows['abuahmadakhlaq']['sources'] += ';別出典'
        from src import master as M
        M.save(rows, path)
        saved = self.snapshot()
        with self.assertRaises(ValueError):
            A.apply(self.root, now=NOW)
        self.assertEqual(saved, self.snapshot())
        approval = self.root / A.APPROVAL
        approval.write_bytes(approval.read_bytes() + b' ')
        saved = self.snapshot()
        with self.assertRaises(ValueError):
            A.apply(self.root, now=NOW)
        self.assertEqual(saved, self.snapshot())

    def test_partial_ledger_withdrawal_or_expiry_cannot_release_more_rows(self):
        path = self.root / L.LEDGER_PATH
        ledger = json.loads(path.read_bytes())
        ledger['entries'] = [r for r in ledger['entries'] if r['master_key'] != 'abuahmadakhlaq']
        path.write_text(json.dumps(ledger))
        saved = self.snapshot()
        with self.assertRaises(ValueError):
            A.apply(self.root, now=NOW)
        self.assertEqual(saved, self.snapshot())
        with self.assertRaises(ValueError):
            A.apply(self.root, now=datetime(2026, 12, 1, tzinfo=timezone.utc))

    def test_failed_projection_keeps_entire_business_generation_unchanged(self):
        saved = self.snapshot()
        with patch.object(A.D, 'write_screening_gzip', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                A.apply(self.root, now=NOW)
        self.assertEqual(saved, self.snapshot())

    def test_io_failure_midway_through_publication_rolls_back_every_file(self):
        from src import persistence as P
        saved = self.snapshot()
        real_replace = P.os.replace
        calls = 0
        def replace(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('replace interrupted')
            return real_replace(source, target)
        with patch.object(P.os, 'replace', side_effect=replace):
            with self.assertRaises(OSError):
                A.apply(self.root, now=NOW)
        self.assertGreaterEqual(calls, 2)
        self.assertEqual(saved, self.snapshot())

    def test_monitor_entrypoint_publishes_once_to_real_origin_before_polling(self):
        script = ROOT / '.github/scripts/apply-approved-legacy-delist.sh'
        def git(*args):
            return subprocess.run(['git', '-C', str(self.root), *args], check=True,
                                  capture_output=True, text=True).stdout.strip()
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        origin = folder / 'origin.git'
        subprocess.run(['git', 'init', '--bare', str(origin)], check=True, capture_output=True)
        git('init', '-b', 'main')
        git('config', 'user.name', 'Tests')
        git('config', 'user.email', 'tests@example.invalid')
        git('add', '.')
        git('commit', '-m', 'baseline')
        git('remote', 'add', 'origin', str(origin))
        git('push', '-u', 'origin', 'main')
        before = git('rev-parse', 'HEAD')
        env = dict(os.environ, GITHUB_REF='refs/heads/main', GITHUB_REPOSITORY='example/repo',
                   GITHUB_RUN_ID='123', PYTHONPATH=str(ROOT) + os.pathsep + os.getenv('PYTHONPATH', ''))
        journal = self.root / A.RECEIPT
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(json.dumps({'status': 'APPLIED', 'count': 6,
                                      'approval_sha256': A.APPROVAL_SHA256}))
        interrupted = subprocess.run(['bash', str(script)], cwd=self.root, env=env, capture_output=True, text=True)
        self.assertNotEqual(interrupted.returncode, 0)
        git('add', A.RECEIPT.as_posix())
        git('commit', '-m', 'unpublished application journal')
        unpushed = subprocess.run(['bash', str(script)], cwd=self.root, env=env, capture_output=True, text=True)
        self.assertNotEqual(unpushed.returncode, 0)
        self.assertEqual(git('ls-remote', 'origin', 'refs/heads/main').split()[0], before)
        git('reset', '--hard', before)
        result = subprocess.run(['bash', str(script)], cwd=self.root, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        published = git('ls-remote', 'origin', 'refs/heads/main').split()[0]
        self.assertNotEqual(published, before)
        rows = V._read_rows(git('show', published + ':data/master/master.csv'))
        self.assertTrue(all(rows[key]['status'] == '無効' for key in SIX))
        changed = set(git('diff', '--name-only', before, published).splitlines())
        self.assertTrue(changed <= set(A.TARGETS) | {A.RECEIPT.as_posix()})
        self.assertIn(A.RECEIPT.as_posix(), changed)
        again = subprocess.run(['bash', str(script)], cwd=self.root, env=env, capture_output=True, text=True)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(git('rev-parse', 'HEAD'), published)
        (self.root / A.RECEIPT).write_text('[]')
        corrupt = subprocess.run(['bash', str(script)], cwd=self.root, env=env, capture_output=True, text=True)
        self.assertNotEqual(corrupt.returncode, 0)
        self.assertEqual(git('ls-remote', 'origin', 'refs/heads/main').split()[0], published)
