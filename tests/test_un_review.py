import copy
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from src import master as M, un_watch as W, un_review as R
from src.normalize import match_key
from tests.test_un_sources import xml, PRESS
from tests.test_un_watch import Feed, NOW


class UNReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.feed = Feed()
        W.run(self.root, fetcher=self.feed, now=NOW)

    def event(self):
        return W.load(self.root)['events'][-1]

    def approve(self):
        event = self.event()
        return R.approve(self.root, event_id=event['event_id'], source_hash=event['source_hash'],
                         reviewer='Operator', when=NOW, note='公式XML・差分を確認',
                         evidence_url='https://main.un.org/securitycouncil/en/content/un-sc-consolidated-list')

    def apply(self, **kwargs):
        event = self.event()
        return R.apply(self.root, event_id=event['event_id'], source_hash=event['source_hash'],
                       expected_master_hash=kwargs.get('expected_master_hash', R.master_hash(self.root)),
                       operator='Operator', when=NOW, note='承認した差分を反映')

    def test_unapproved_or_wrong_master_hash_cannot_change_master(self):
        with self.assertRaises(ValueError):
            self.apply()
        self.approve()
        with self.assertRaises(ValueError):
            self.apply(expected_master_hash='0' * 64)
        self.assertFalse((self.root / 'data/master/master.csv').exists())

    def test_low_alias_stays_in_evidence_but_not_master(self):
        self.feed.body = xml(quality='Low')
        W.run(self.root, fetcher=self.feed, now=NOW)
        self.approve()
        result = self.apply()
        rows = M.load(self.root / 'data/master/master.csv')
        self.assertIn(match_key('Alice Example'), rows)
        self.assertIn(match_key('アリス'), rows)
        self.assertNotIn(match_key('Ally'), rows)
        self.assertIn('Low', (self.root / 'data/un/snapshot.json').read_text())
        self.assertEqual(result['applied_event_id'], self.event()['event_id'])
        self.assertIn('初回同期（国連）', (self.root / 'data/diff/latest.csv').read_text())

    def test_old_approval_does_not_apply_after_source_reverts(self):
        self.approve()
        old = copy.deepcopy(self.event())
        self.feed.body = xml(comment='changed')
        W.run(self.root, fetcher=self.feed, now=NOW)
        self.feed.body = xml()
        W.run(self.root, fetcher=self.feed, now=NOW)
        with self.assertRaises(ValueError):
            self.apply()
        with self.assertRaises(ValueError):
            R.approve(self.root, event_id=old['event_id'], source_hash=old['source_hash'],
                      reviewer='Operator', when=NOW, note='old event', evidence_url='https://main.un.org/x')

    def test_alias_removed_only_after_approval_other_source_and_shared_party_survive(self):
        rows = {}
        M.merge(rows, [dict(name='Ally', category='test')], '財務省', ts=1000)
        M.save(rows, self.root / 'data/master/master.csv')
        self.approve()
        self.apply()
        self.feed.body = xml(alias='New Alias')
        W.run(self.root, fetcher=self.feed, now=NOW)
        before = M.load(self.root / 'data/master/master.csv')
        self.assertIn('国連', before[match_key('Ally')]['sources'])
        self.approve()
        self.apply()
        after = M.load(self.root / 'data/master/master.csv')
        self.assertEqual(after[match_key('Ally')]['status'], '有効')
        self.assertEqual(after[match_key('Ally')]['sources'], '財務省')
        self.assertEqual(after[match_key('Ally')]['first_seen_ms'], '1000')
        self.assertIn(match_key('New Alias'), after)

    def test_remaining_strong_same_name_on_other_party_prevents_delist(self):
        self.feed.body = xml().replace(b'<FIRST_NAME>Example Ltd</FIRST_NAME>', b'<FIRST_NAME>Ally</FIRST_NAME>')
        W.run(self.root, fetcher=self.feed, now=NOW)
        self.approve()
        self.apply()
        self.feed.body = self.feed.body.replace(b'<ALIAS_NAME>Ally</ALIAS_NAME>', b'<ALIAS_NAME>New Alias</ALIAS_NAME>')
        W.run(self.root, fetcher=self.feed, now=NOW)
        self.approve()
        self.apply()
        after = M.load(self.root / 'data/master/master.csv')
        self.assertEqual(after[match_key('Ally')]['status'], '有効')
        self.assertIn('国連', after[match_key('Ally')]['sources'])

    def test_network_error_or_stale_observation_blocks_approval(self):
        self.feed.fail_xml = True
        W.run(self.root, fetcher=self.feed, now=NOW)
        with self.assertRaises(ValueError):
            self.approve()
        self.feed.fail_xml = False
        W.run(self.root, fetcher=self.feed, now=NOW)
        event = self.event()
        with self.assertRaises(ValueError):
            R.approve(self.root, event_id=event['event_id'], source_hash=event['source_hash'], reviewer='Operator',
                      when=NOW + timedelta(hours=2), note='stale', evidence_url='https://main.un.org/x')

    def test_notice_review_does_not_apply_xml_or_clear_other_notices(self):
        previous = PRESS.split(b'<tr>')[1].split(b'</tr>')[0]
        self.feed.press = PRESS.replace(b'sc16488', b'sc16499').replace(b'</tbody>', b'<tr>' + previous + b'</tr></tbody>')
        W.run(self.root, fetcher=self.feed, now=NOW)
        pending = [n for n in W.load(self.root)['notices'].values() if n['status'] == 'PENDING_REVIEW']
        n = pending[0]
        R.review_notice(self.root, event_id=n['event_id'], index_hash=n['index_hash'], reviewer='Operator',
                        when=NOW, note='公式発表とXML反映状況を確認')
        state = W.load(self.root)
        self.assertEqual(state['notices'][n['event_id']]['status'], 'REVIEWED')
        self.assertEqual(state['xml_status'], 'review_required')
        self.assertEqual(state['press_status'], 'review_required')  # other committees remain pending
        self.assertFalse((self.root / 'data/master/master.csv').exists())

    def test_latest_review_includes_removal_from_skipped_observation(self):
        self.approve()
        self.apply()
        self.feed.body = xml(ref='QDi.002')
        W.run(self.root, fetcher=self.feed, now=NOW)
        self.feed.body = xml(ref='QDi.002', comment='later metadata only')
        W.run(self.root, fetcher=self.feed, now=NOW)
        event = self.event()
        changes = {row['reference']: row for row in event['application_diff']}
        self.assertEqual(changes['QDi.001']['change'], '掲載終了候補')
        self.assertEqual(changes['QDi.002']['change'], '追加')
        self.assertIn('QDi.001', (self.root / 'data/dashboard/un_reviews.csv').read_text())

    def test_monitor_cannot_advance_the_same_root_during_application(self):
        self.approve()
        before = (self.root / W.STATE_PATH).read_bytes()
        original = R.D.write_screening_gzip
        def advance(stage, rows):
            original(stage, rows)
            self.feed.body = xml(name='Bob Example')
            W.run(self.root, fetcher=self.feed, now=NOW)
        with patch.object(R.D, 'write_screening_gzip', advance), self.assertRaises(RuntimeError):
            self.apply()
        self.assertEqual((self.root / W.STATE_PATH).read_bytes(), before)
        self.assertFalse((self.root / 'data/master/master.csv').exists())


if __name__ == '__main__':
    unittest.main()
