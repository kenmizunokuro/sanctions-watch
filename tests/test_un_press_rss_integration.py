import copy
import csv
import hashlib
import json
import tempfile
import unittest
import contextlib
import io
import os
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from src import un_watch as W, un_review as R, un_press_rss as P
from tests.test_un_watch import Feed, NOW
from tests.test_un_press_rss import rss


class RSSIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.feed=Feed()
        self.master=self.root/'data/master/master.csv';self.master.parent.mkdir(parents=True)
        self.master.write_bytes(b'unchanged master')
    def run_watch(self,now=NOW,fetcher=None):return W.run(self.root,fetcher=fetcher or self.feed,now=now)
    def state(self):return W.load(self.root)
    def candidates(self):return [e for e in self.state()['press_rss']['events'] if e['status']=='PENDING_REVIEW']
    def reviews(self):
        with (self.root/'data/dashboard/un_reviews.csv').open() as f:return list(csv.DictReader(f))
    def changed(self):
        self.feed.rss=rss([('sc16499','Security Council Sanctions Committee Amends Entry'),
                           ('sc16500','Security Council Sanctions Committee Adds Entry')])

    def test_baseline_summary_304_and_candidates_preserve_master_and_first_detection(self):
        self.assertTrue(self.run_watch()['ok'])
        self.assertIn('press_rss',self.state()['documents'])
        self.assertEqual(self.state()['press_rss']['new_candidates'],0)
        self.assertTrue(any(r['イベントID']=='rss:status' for r in self.reviews()))
        self.changed();self.run_watch(now=NOW+timedelta(minutes=15))
        event=copy.deepcopy(self.candidates()[0])
        self.feed.not_modified=True;self.run_watch(now=NOW+timedelta(minutes=30))
        self.assertEqual(self.candidates(),[event]);self.assertEqual(self.master.read_bytes(),b'unchanged master')
        row=next(r for r in self.reviews() if r['イベントID']=='rss:'+event['event_id'])
        self.assertEqual(row['検知日時'],'2026-10-27T01:15:00Z')

    def test_rss_success_does_not_clear_primary_html_failures(self):
        self.run_watch();self.changed()
        def getter(url,**kw):
            f=self.feed(url,**kw)
            if url.startswith('https://main.un.org/'):
                f.http_status=202;f.body=b'';f.sha256=hashlib.sha256(b'').hexdigest()
                f.headers={'x-amzn-waf-action':'challenge'}
            return f
        result=self.run_watch(fetcher=getter)
        self.assertFalse(result['ok']);self.assertEqual(result['press_status'],'error')
        self.assertEqual(result['updates_status'],'error')
        self.assertEqual(len(result['failures']),15)
        self.assertEqual(self.state()['press_rss']['status'],'observed')
        self.assertEqual(len(self.candidates()),1)
        self.assertEqual(self.master.read_bytes(),b'unchanged master')

    def test_malformed_challenged_rss_preserves_document_and_prior_candidates(self):
        self.run_watch();self.changed();self.run_watch()
        before=copy.deepcopy(self.state())
        for challenge in [False,True]:
            def getter(url,**kw):
                f=self.feed(url,**kw)
                if url==P.URL:
                    f.body=b'<html/>';f.sha256=hashlib.sha256(f.body).hexdigest()
                    if challenge:f.headers={'x-amzn-waf-action':'challenge'};f.http_status=202
                return f
            result=self.run_watch(fetcher=getter)
            self.assertFalse(result['ok']);self.assertEqual(self.state()['press_rss']['status'],'error')
            self.assertEqual(self.state()['documents']['press_rss'],before['documents']['press_rss'])
            self.assertEqual(self.state()['press_rss']['events'],before['press_rss']['events'])
            self.assertTrue(any(r['イベントID']=='rss:fetch' for r in self.reviews()))
            with (self.root/'data/source_audit/2026-10.csv').open() as f:
                row=[r for r in csv.DictReader(f) if r['source']=='un_press_rss'][-1]
            self.assertEqual(row['fetch_failed'],'1' if challenge else '0')
            self.assertEqual(row['schema_changed'],'0' if challenge else '1')

    def test_stale_and_coverage_gap_are_visible_and_gap_requires_explicit_review(self):
        self.run_watch();self.changed();self.run_watch(now=NOW+timedelta(minutes=91))
        sensor=self.state()['press_rss'];self.assertEqual(sensor['status'],'coverage_gap')
        self.assertTrue(any(r['イベントID']=='rss:coverage' for r in self.reviews()))
        self.run_watch(now=NOW+timedelta(minutes=92))
        self.assertEqual(self.state()['press_rss']['coverage_gap'],sensor['coverage_gap'])
        with self.assertRaises(ValueError):R.review_rss_coverage(self.root,index_hash='0'*64,
            reviewer='Reviewer',when=NOW+timedelta(minutes=93),note='checked')
        R.review_rss_coverage(self.root,index_hash=self.state()['documents']['press_rss']['sha256'],
            reviewer='Reviewer',when=NOW+timedelta(minutes=93),note='all missing period checked in official archive')
        self.assertNotIn('coverage_gap',self.state()['press_rss'])
        result=self.run_watch(now=NOW+timedelta(hours=73))
        self.assertFalse(result['ok']);self.assertEqual(self.state()['press_rss']['status'],'stale')
        self.assertTrue(any(r['イベントID']=='rss:status' and r['状態']=='STALE' for r in self.reviews()))

    def test_hash_bound_rss_review_cannot_approve_xml_or_change_master(self):
        self.run_watch();self.changed();self.run_watch();e=self.candidates()[0]
        xml_before=copy.deepcopy(self.state()['events'])
        with self.assertRaises(ValueError):R.review_rss_notice(self.root,event_id=e['event_id'],index_hash='0'*64,
            reviewer='Reviewer',when=NOW,note='checked')
        R.review_rss_notice(self.root,event_id='rss:'+e['event_id'],index_hash=e['source_hash'],
            reviewer='Reviewer',when=NOW,note='verified original announcement; master not applied')
        self.assertEqual(self.candidates(),[])
        self.assertEqual(self.state()['events'],xml_before)
        self.assertEqual(self.master.read_bytes(),b'unchanged master')

    def test_coverage_review_rechecks_publication_age_at_review_time(self):
        self.feed.rss=self.feed.rss.replace(b'Tue, 27 Oct 2026 00:30:00',b'Sat, 24 Oct 2026 01:30:00')
        self.run_watch(now=NOW-timedelta(minutes=91));self.run_watch()
        self.assertFalse(self.state()['press_rss']['stale'])
        with self.assertRaises(ValueError):R.review_rss_coverage(self.root,
            index_hash=self.state()['documents']['press_rss']['sha256'],reviewer='Reviewer',
            when=NOW+timedelta(hours=1),note='publication has become stale since the poll')

    def test_cli_baseline_new_and_repeated_counts_are_published_once(self):
        original=W.run
        output=self.root/'outputs.txt'
        def run(root):return original(root,fetcher=self.feed,now=NOW)
        with patch.object(W,'run',side_effect=run),patch('sys.argv',['un_watch','--root',str(self.root)]),\
             patch.dict(os.environ,{'GITHUB_OUTPUT':str(output)}),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(W.main(),0)
            self.assertIn('new_press_rss=0',output.read_text())
            output.unlink();self.changed()
            self.assertEqual(W.main(),0)
            self.assertIn('new_press_rss=1',output.read_text())
            output.unlink();self.assertEqual(W.main(),0)
            self.assertIn('new_press_rss=0',output.read_text())

    def test_status_cli_exposes_hash_bound_displayed_rss_candidate_ids(self):
        self.run_watch();self.changed();self.run_watch()
        out=io.StringIO()
        with patch('sys.argv',['un_review','status','--root',str(self.root)]),contextlib.redirect_stdout(out):
            self.assertEqual(R.main(),0)
        result=json.loads(out.getvalue())
        self.assertEqual(result['press_rss']['status'],'observed')
        self.assertEqual(result['press_rss']['pending'][0]['event_id'],'rss:'+self.candidates()[0]['event_id'])
        self.assertEqual(result['press_rss']['pending'][0]['source_hash'],self.candidates()[0]['source_hash'])


if __name__=='__main__':unittest.main()
