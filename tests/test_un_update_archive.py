import csv
import gzip
import hashlib
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from src import un_press_rss as P, un_updates as V, un_watch as W
from tests.test_un_sources import UPDATES_WITH_CONFLICT
from tests.test_un_watch import Feed, NOW


class UNUpdateArchiveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.feed = Feed()
        self.archive = self.root / 'data/raw/un'
        self.archive.mkdir(parents=True)
        receipt_dir = self.root / 'data/un'
        receipt_dir.mkdir()
        self.path = receipt_dir / 'updates_archive_receipt.json'
        self.sha = hashlib.sha256(UPDATES_WITH_CONFLICT).hexdigest()
        self.raw = self.archive / f'{self.sha}.html.gz'
        self.raw.write_bytes(gzip.compress(UPDATES_WITH_CONFLICT))
        self.receipt = dict(schema_version=1, capture_method='alternate_https', http_status=200,
                            url=V.UPDATES_URL, final_url=V.UPDATES_URL,
                            observed_at=P.stamp(NOW), source_hash=self.sha,
                            etag='archive-tag', last_modified='', record_count=4, conflict_count=1)
        self.path.write_text(json.dumps(self.receipt), encoding='utf-8')

    def fetcher(self, url, **kwargs):
        result = self.feed(url, **kwargs)
        if url == V.UPDATES_URL:
            result.http_status = 202
            result.body = b''
            result.headers = {'x-amzn-waf-action': 'challenge'}
        return result

    def poll(self, minutes=60):
        return W.run(self.root, fetcher=self.fetcher, now=NOW + timedelta(minutes=minutes))

    def test_record_archived_candidates_preserving_original_observation_and_live_failure(self):
        result = self.poll()
        self.assertFalse(result['ok'])
        state = W.load(self.root)
        self.assertEqual(state['updates_status'], 'error')
        self.assertNotIn('updates_last_success_at', state)
        self.assertFalse({'updates', 'updates_partial'} & state['documents'].keys())
        self.assertEqual(len(state['updates_events']), 4)
        conflict_id, = state['updates_conflicts']
        event = state['updates_conflicts'][conflict_id]
        self.assertEqual(event['detected_at'], self.receipt['observed_at'])
        self.assertEqual(event['source_hash'], self.sha)
        self.assertEqual(event['status'], 'REVIEW_REQUIRED')
        self.assertEqual(state['updates_archive_recoveries'][self.sha]['imported_at'],
                         P.stamp(NOW + timedelta(minutes=60)))
        with (self.root / 'data/dashboard/un_reviews.csv').open(encoding='utf-8') as stream:
            rows = {row['イベントID']: row for row in csv.DictReader(stream)}
        self.assertIn('updates:fetch', rows)
        self.assertEqual(rows['updates:conflict:' + conflict_id]['国連参照番号'], 'QDe.010')

        self.poll(75)
        later = W.load(self.root)
        self.assertEqual(later['updates_events'], state['updates_events'])
        self.assertEqual(later['updates_conflicts'], state['updates_conflicts'])
        self.assertEqual(later['updates_archive_recoveries'], state['updates_archive_recoveries'])
        with (self.root / 'data/source_audit/2026-10.csv').open(encoding='utf-8') as stream:
            audit = list(csv.DictReader(stream))
        self.assertEqual(sum(r['source']=='un_updates_archive' for r in audit),1)
        self.assertFalse((self.root / 'data/master/master.csv').exists())

    def test_original_integrity_and_receipt_metadata_fail_closed(self):
        self.raw.write_bytes(gzip.compress(b'wrong body'))
        result = self.poll()
        self.assertTrue(any(f['source']=='un_updates_archive' for f in result['failures']))
        self.assertFalse(W.load(self.root).get('updates_conflicts'))
        self.raw.write_bytes(gzip.compress(UPDATES_WITH_CONFLICT))
        self.receipt['record_count'] = 1
        self.path.write_text(json.dumps(self.receipt), encoding='utf-8')
        self.poll(75)
        self.assertFalse(W.load(self.root).get('updates_events'))

    def test_existing_live_candidate_is_not_overwritten_or_announced_again(self):
        self.path.unlink()
        self.feed.updates = UPDATES_WITH_CONFLICT
        W.run(self.root, fetcher=self.feed, now=NOW + timedelta(minutes=5))
        original = W.load(self.root)['updates_events']
        self.path.write_text(json.dumps(self.receipt), encoding='utf-8')
        self.poll()
        self.assertEqual(W.load(self.root)['updates_events'], original)

    def test_cli_does_not_report_archived_candidates_as_fresh_notifications(self):
        output = self.root / 'outputs.txt'
        argv = ['un_watch', '--root', str(self.root)]
        run = W.run
        with patch('sys.argv', argv), patch.dict('os.environ', {'GITHUB_OUTPUT': str(output)}), \
                patch.object(W, 'run', side_effect=lambda root: run(root, fetcher=self.fetcher,
                                                              now=NOW + timedelta(minutes=60))):
            self.assertEqual(W.main(), 1)
        self.assertIn('new_updates=0\n', output.read_text())
