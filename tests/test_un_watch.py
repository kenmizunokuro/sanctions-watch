import copy
import csv
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src import un_sources as U, un_watch as W
from src.fetch import Fetched
from src.dashboard import build_status_rows
from src.dashboard import write_status_rows
from tests.test_un_sources import xml, PRESS

NOW = datetime(2026, 10, 27, 1, tzinfo=timezone.utc)


class Feed:
    def __init__(self):
        self.body = xml()
        self.press = PRESS
        self.not_modified = False
        self.fail_xml = False

    def __call__(self, url, *, prev=None, **kwargs):
        if url == U.XML_URL and self.fail_xml:
            raise TimeoutError('network unavailable')
        body = self.body if url == U.XML_URL else self.press
        return Fetched(url, body=None if self.not_modified else body,
                       sha256=(prev or {}).get('sha256', '') if self.not_modified else hashlib.sha256(body).hexdigest(),
                       not_modified=self.not_modified, etag='test-etag',
                       filename='consolidated.xml' if url == U.XML_URL else 'press.html',
                       http_status=304 if self.not_modified else 200, final_url=url)


class UNWatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.feed = Feed()

    def run_watch(self):
        return W.run(self.root, fetcher=self.feed, now=NOW)

    def state(self):
        return json.loads((self.root / 'data/un/state.json').read_text())

    def test_baseline_then_304_keeps_review_and_no_master_mutation(self):
        result = self.run_watch()
        self.assertTrue(result['ok'])
        state = self.state()
        event = state['events'][-1]
        self.assertTrue(event['baseline'])
        self.assertEqual(event['added'], ['QDe.001', 'QDi.001'])
        self.assertEqual(event['status'], 'PENDING_REVIEW')
        self.assertFalse((self.root / 'data/master/master.csv').exists())
        self.feed.not_modified = True
        self.run_watch()
        self.assertEqual(len(self.state()['events']), 1)
        self.assertIn('初回収録', (self.root / 'data/dashboard/un_reviews.csv').read_text())
        rows = build_status_rows(self.root, [], {})
        un = next(r for r in rows if r[0] == '国連（統合リスト）')
        self.assertIn('要レビュー', un[1])
        self.assertEqual(un[4], '2')

    def test_un_observation_preserves_other_source_status_exactly(self):
        existing = build_status_rows(self.root, [], {})
        existing[1] = ['経済産業省', '自動取得不可', '2026-10-20 12:00:00', 'old', '100', 'abc']
        write_status_rows(self.root / 'data/dashboard/status.csv', existing)
        self.run_watch()
        with (self.root / 'data/dashboard/status.csv').open() as file:
            rows = list(csv.reader(file))[1:]
        self.assertEqual(rows[:6], existing)

    def test_metadata_changes_are_events_and_hash_reversion_needs_new_review(self):
        self.run_watch()
        first = copy.deepcopy(self.state()['events'][-1])
        self.feed.body = xml(comment='new identity document')
        self.run_watch()
        self.assertEqual(self.state()['events'][-1]['changed'], ['QDi.001'])
        self.assertEqual(self.state()['events'][0]['status'], 'SUPERSEDED')
        self.feed.body = xml()
        self.run_watch()
        last = self.state()['events'][-1]
        self.assertEqual(first['source_hash'], last['source_hash'])
        self.assertNotEqual(first['event_id'], last['event_id'])
        self.assertEqual(last['status'], 'PENDING_REVIEW')

    def test_corrupt_304_cache_fails_and_keeps_previous_snapshot(self):
        self.run_watch()
        state = self.state()
        (self.root / state['documents']['xml']['raw_path']).write_bytes(b'corrupt')
        self.feed.not_modified = True
        result = self.run_watch()
        self.assertFalse(result['ok'])
        self.assertEqual(self.state()['snapshot_hash'], state['snapshot_hash'])
        self.assertEqual(self.state()['xml_status'], 'error')
        with (self.root / 'data/source_audit/2026-10.csv').open() as file:
            audit = list(csv.DictReader(file))
        row = [r for r in audit if r['source'] == 'un_xml'][-1]
        self.assertEqual(row['http_status'], '304')
        self.assertEqual(row['raw_path'], state['documents']['xml']['raw_path'])

    def test_empty_response_preserves_http_evidence_without_advancing_cache(self):
        def fetcher(url, **kwargs):
            result = self.feed(url, **kwargs)
            if url != U.XML_URL:
                result.body = b''
                result.sha256 = hashlib.sha256(b'').hexdigest()
                result.http_status = 202
            return result
        result = W.run(self.root, fetcher=fetcher, now=NOW)
        self.assertFalse(result['ok'])
        self.assertEqual(self.state()['press_status'], 'error')
        self.assertFalse(any(key.startswith('press:') for key in self.state()['documents']))
        with (self.root / 'data/source_audit/2026-10.csv').open() as file:
            rows = [r for r in csv.DictReader(file) if r['source'] == 'un_press']
        self.assertEqual(len(rows), 14)
        for row in rows:
            self.assertEqual(row['http_status'], '202')
            self.assertEqual(row['content_hash'], 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')
            self.assertTrue((self.root / row['raw_path']).is_file())

    def test_schema_and_network_failures_preserve_valid_snapshot_and_raw_evidence(self):
        self.run_watch()
        before = (self.root / 'data/un/snapshot.json').read_bytes()
        for malformed in [True, False]:
            self.feed.body = b'<html>denied</html>'
            self.feed.fail_xml = not malformed
            self.assertFalse(self.run_watch()['ok'])
            self.assertEqual((self.root / 'data/un/snapshot.json').read_bytes(), before)
        audit = (self.root / 'data/source_audit/2026-10.csv').read_text()
        self.assertIn('UNSchemaError', audit)
        self.assertIn('TimeoutError', audit)
        self.assertEqual(self.state()['xml_status'], 'error')

    def test_notice_sensor_keeps_pending_when_xml_has_no_change(self):
        self.run_watch()
        self.feed.press = PRESS.replace(b'sc16488', b'sc16499').replace(b'removes three', b'adds one') + b' '
        self.assertFalse(self.run_watch()['ok'])  # lost entire prior page: explicit coverage gap
        self.assertIn('1267', self.state()['coverage_gaps'])
        # A shared previous item makes the next scan complete, but does not erase the gap.
        previous_row = PRESS.split(b'<tr>')[1].split(b'</tr>')[0]
        self.feed.press = self.feed.press.replace(b'</tbody>', b'<tr>' + previous_row + b'</tr></tbody>')
        self.run_watch()
        self.assertEqual(self.state()['press_status'], 'error')
        pending = [n for n in self.state()['notices'].values() if n['status'] == 'PENDING_REVIEW']
        self.assertTrue(any('sc16499' in n['url'] for n in pending))

    def test_alias_loss_and_party_removal_are_reviewed_not_applied(self):
        self.run_watch()
        self.feed.body = xml().replace(b'<INDIVIDUAL_ALIAS><QUALITY>Good</QUALITY><ALIAS_NAME>Ally</ALIAS_NAME></INDIVIDUAL_ALIAS>', b'')
        self.run_watch()
        self.assertEqual(self.state()['events'][-1]['changed'], ['QDi.001'])
        self.feed.body = xml().replace(b'QDi.001', b'QDi.002')
        self.run_watch()
        self.assertEqual(self.state()['events'][-1]['removed'], ['QDi.001'])
        self.assertFalse((self.root / 'data/master/master.csv').exists())

    def test_name_evidence_preserves_low_alias_history_and_first_seen(self):
        self.feed.body = xml(quality='Low')
        self.run_watch()
        self.feed.body = xml(alias='New Alias')
        self.run_watch()
        with (self.root / 'data/un/name_history.csv').open() as file:
            rows = list(csv.DictReader(file))
        old = next(r for r in rows if r['name'] == 'Ally')
        self.assertEqual(old['weak'], '1')
        self.assertEqual(old['current'], '0')
        self.assertEqual(old['reference'], 'QDi.001')
        self.assertEqual(old['first_seen'], '2026-10-27T01:00:00Z')
        self.assertTrue(old['source_hash'])
        self.assertTrue((self.root / old['raw_path']).exists())

    def test_atomic_failure_does_not_publish_half_a_bundle(self):
        self.run_watch()
        before = (self.root / 'data/un/state.json').read_bytes()
        self.feed.body = xml(comment='changed')
        with patch('src.un_watch.atomic_replace_many', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.run_watch()
        self.assertEqual((self.root / 'data/un/state.json').read_bytes(), before)

    def test_identical_200_does_not_rewrite_and_damage_valid_archive(self):
        self.run_watch()
        archive = self.root / self.state()['documents']['xml']['raw_path']
        before = archive.read_bytes()
        original = Path.write_bytes
        def partial_write(path, body):
            if path == archive:
                original(path, b'partial write')
                raise OSError('disk full')
            return original(path, body)
        with patch.object(Path, 'write_bytes', partial_write):
            result = self.run_watch()
        self.assertEqual(archive.read_bytes(), before)
        self.assertTrue(result['ok'])

    def test_repeated_notice_reversions_keep_distinct_event_history(self):
        self.run_watch()
        for title in [b'Committee adds one entry', b'Committee removes three entries', b'Committee adds one entry']:
            self.feed.press = PRESS.replace(b'Committee removes three entries', title)
            self.run_watch()
        self.assertEqual(len(self.state()['notices']), 56)


if __name__ == '__main__':
    unittest.main()
