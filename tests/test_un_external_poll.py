import contextlib
import io
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from src import un_external_poll as E, un_watch as W, un_updates as V, un_sources as U
from src import un_watch_notify as N
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
        with contextlib.redirect_stdout(io.StringIO()), patch.object(
            W, 'run', side_effect=lambda root: actual_run(root, fetcher=fetcher or self.feed, now=NOW)
        ):
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


    def test_current_failure_rejects_the_external_receipt_even_with_same_second_success(self):
        for rejected_url in (U.XML_URL, U.press_url('1267'), V.UPDATES_URL):
            with self.subTest(url=rejected_url):
                self.assertTrue(self.collect())
                def blocked(url, **kwargs):
                    if url == rejected_url:
                        return Fetched(url, http_status=202, body=b'', final_url=url,
                                       headers={'x-amzn-waf-action':'challenge'})
                    return self.feed(url, **kwargs)
                self.assertFalse(self.collect(blocked))
                self.assertFalse(E.recent(self.root, now=NOW))

    def test_published_notice_uses_existing_detection_channel_and_exact_parent(self):
        old = W.load(self.root)
        self.assertTrue(self.collect())
        sha = 'a' * 40
        prior = CompletedProcess([], 0, stdout=json.dumps(old).encode())
        with patch.object(E.subprocess, 'run', return_value=prior) as git, patch.object(N, 'post') as post:
            E.notify_published(self.root, sha)
            git.assert_called_once_with(['git', 'show', sha + ':' + W.STATE_PATH], cwd=self.root,
                                        capture_output=True, check=True)
            calls = [args.args[0] for args in post.call_args_list]
        self.assertTrue(any('国連 XML検証済' in line for line in calls))
        self.assertTrue(any('国連 公式更新履歴検知' in line for line in calls))
        self.assertTrue(any('国連 監視エラー' in line for line in calls))
        with self.assertRaises(ValueError), patch.object(N, 'post') as post:
            E.notify_published(self.root, '--not-a-sha')
        post.assert_not_called()
