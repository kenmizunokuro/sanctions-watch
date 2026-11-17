import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from src import un_external_poll as E, un_watch as W, un_updates as V
from src.fetch import Fetched
from tests.test_un_sources import UPDATES_WITH_CONFLICT
from tests.test_un_watch import Feed, NOW


class ExternalPollTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.feed = Feed()
        self.feed.updates = UPDATES_WITH_CONFLICT
        self.known_master = self.root / 'data/master/master.csv'
        self.known_master.parent.mkdir(parents=True)
        self.known_master.write_bytes(b'master is untouched')

    def collect(self, fetcher=None):
        actual_run = W.run
        with patch.object(W, 'run', side_effect=lambda root: actual_run(root, fetcher=fetcher or self.feed, now=NOW)):
            return E.collect(self.root)

    def test_all_sources_reached_treats_reference_conflict_as_visible_error(self):
        self.assertTrue(self.collect())
        state = W.load(self.root)
        self.assertEqual(state['updates_status'], 'error')
        self.assertTrue(state['updates_active_conflicts'])
        self.assertEqual(state['press_last_success_at'], state['last_checked_at'])
        self.assertTrue(E.recent(self.root, now=NOW+timedelta(minutes=34)))
        self.assertFalse(E.recent(self.root, now=NOW+timedelta(minutes=35)))
        self.assertFalse(E.recent(self.root, now=NOW-timedelta(seconds=1)))
        self.assertEqual(self.known_master.read_bytes(), b'master is untouched')

    def test_partial_transport_cannot_claim_external_ownership(self):
        def blocked(url, **kwargs):
            if url == V.UPDATES_URL:
                return Fetched(url, http_status=202, body=b'', final_url=url,
                               headers={'x-amzn-waf-action':'challenge'})
            return self.feed(url, **kwargs)
        self.assertFalse(self.collect(blocked))
        self.assertFalse((self.root / E.RECEIPT).exists())
        self.assertFalse(E.recent(self.root, now=NOW))

    def test_later_generation_or_bad_receipt_reenables_github_collection(self):
        self.assertTrue(self.collect())
        W.run(self.root, fetcher=self.feed, now=NOW + timedelta(minutes=1))
        self.assertFalse(E.recent(self.root, now=NOW+timedelta(minutes=2)))
        (self.root / E.RECEIPT).write_text('not json')
        self.assertFalse(E.recent(self.root, now=NOW+timedelta(minutes=2)))
