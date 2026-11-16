import copy
import csv
import gzip
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

from src import un_watch as W, un_review as R, un_press_rss as P, source_audit as A
from src.fetch import Fetched
from tests.test_un_watch import Feed, NOW
from tests.test_un_press_rss import rss


URL = 'https://press.un.org/en/2026/sc16506.doc.htm'
TITLE = 'Security Council 1533 Sanctions Committee Amends One Entity on Its Sanctions List'
BODY = f'<html><h1>{TITLE}</h1><div>SC/16506</div></html>'.encode()


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

    def archive_missing_candidate(self):
        receipt_time = NOW + timedelta(minutes=5)
        article_url = 'https://press.un.org/en/2026/sc16508.doc.htm'
        body = rss([('sc16508', 'Security Council Updates Yemen Sanctions')])
        digest = hashlib.sha256(body).hexdigest()
        raw_path = f'data/raw/un/{digest}.xml.gz'
        (self.root / raw_path).write_bytes(gzip.compress(body))
        manifest = {
            'schema_version': 1, 'article_url': article_url, 'source_hash': digest,
            'source_received_at': P.stamp(receipt_time),
            'official_reference_url': 'https://main.un.org/securitycouncil/en/content/resolutions-0',
            'official_reference': 'S/RES/2837 (2026)',
        }
        (self.root / 'data/un/rss_archived_candidate.json').write_text(json.dumps(manifest))
        audit = A.entry('un_press_rss', 'auxiliary_global_news', 'error',
                        url=P.URL, content_hash=digest, raw_path=raw_path,
                        schema_changed=True)
        audit.update(http_status=200, final_url=P.URL, error_type='UNSchemaError')
        A.append_rows(self.root / 'data/source_audit/2026-10.csv', [audit], now=receipt_time)
        return manifest

    def test_archive_recovery_publishes_review_candidate_once_with_distinct_times(self):
        self.run_watch()
        request = self.archive_missing_candidate()
        now = NOW + timedelta(minutes=120)
        self.run_watch(now=now)
        event, = self.candidates()
        self.assertEqual(event['detected_at'], P.stamp(now))
        self.assertEqual(event['source_received_at'], request['source_received_at'])
        self.assertEqual(event['record']['url'], request['article_url'])
        self.assertEqual(event['source_hash'], request['source_hash'])
        self.assertEqual(self.state()['press_rss']['new_candidates'], 0)
        self.assertEqual(self.state()['press_rss']['status'], 'coverage_gap')
        row = next(r for r in self.reviews() if r['イベントID'] == 'rss:' + event['event_id'])
        self.assertEqual(row['区分'], '国連公式RSS・過去取得分の復元候補')
        self.run_watch(now=now+timedelta(minutes=15))
        self.assertEqual(self.candidates(), [event])
        self.assertEqual(self.master.read_bytes(), b'unchanged master')
        state = self.state()
        state['press_rss']['events'][-1]['source_received_at'] = P.stamp(NOW)
        W._write_json(self.root / W.STATE_PATH, state)
        with self.assertRaisesRegex(ValueError, 'archive recovery provenance'):
            R.review_rss_notice(self.root, event_id=event['event_id'], index_hash=event['source_hash'],
                               reviewer='operator', when=now+timedelta(minutes=20), note='official source checked')
        state['press_rss']['events'][-1] = copy.deepcopy(event)
        W._write_json(self.root / W.STATE_PATH, state)
        reviewed = R.review_rss_notice(self.root, event_id=event['event_id'],
                                        index_hash=event['source_hash'], reviewer='operator',
                                        when=now+timedelta(minutes=20), note='official source checked')
        self.assertEqual(reviewed['status'], 'REVIEWED')

    def test_article_reappearing_in_live_feed_keeps_the_archived_recovery_origin(self):
        self.run_watch()
        request = self.archive_missing_candidate()
        self.feed.rss = rss([
            ('sc16499', 'Security Council Sanctions Committee Amends Entry'),
            ('sc16508', 'Security Council Updates Yemen Sanctions'),
        ])
        self.run_watch(now=NOW+timedelta(minutes=15))
        event, = self.candidates()
        self.assertEqual(event['source_hash'], request['source_hash'])
        self.assertEqual(event['source_received_at'], request['source_received_at'])
        self.assertEqual(self.state()['press_rss']['new_candidates'], 0)

    def test_archive_recovery_rejects_a_mismatched_historical_poll(self):
        self.run_watch()
        request = self.archive_missing_candidate()
        request['source_received_at'] = P.stamp(NOW+timedelta(minutes=6))
        (self.root / 'data/un/rss_archived_candidate.json').write_text(json.dumps(request))
        result = self.run_watch(now=NOW+timedelta(minutes=15))
        self.assertFalse(result['ok'])
        self.assertFalse(self.candidates())
        self.assertEqual(self.state()['press_rss']['status'], 'observed')
        self.assertTrue(any(r['イベントID'] == 'rss:recovery' for r in self.reviews()))

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


    def prepare(self):
        self.run_watch()
        self.feed.rss = rss([('sc16499', 'Security Council Sanctions Committee Amends Entry'),
                             ('sc16506', TITLE)])
        self.requests = []

    def getter(self, body=BODY, *, response_url=URL, status=200, headers=None):
        def fetch(url, **kwargs):
            if url != URL:
                return self.feed(url, **kwargs)
            kwargs['url_validator'](url)
            self.assertFalse(kwargs['allow_conditional'])
            self.requests.append(url)
            return Fetched(url, body=body, sha256=hashlib.sha256(body).hexdigest(),
                           http_status=status, final_url=response_url, headers=headers or {},
                           etag='article-tag', last_modified='Wed, 11 Nov 2026 23:00:00 GMT')
        return fetch

    def test_archives_article_separately_and_keeps_first_detection_and_review(self):
        self.prepare()
        observed = NOW + timedelta(minutes=15)
        self.run_watch(now=observed, fetcher=self.getter())
        event, = self.candidates()
        receipt = self.state()['press_rss']['article_evidence'][event['event_id']]
        self.assertEqual(receipt['verified_at'], P.stamp(observed))
        self.assertEqual(receipt['source_hash'], event['source_hash'])
        self.assertEqual(receipt['http_status'], 200)
        self.assertEqual(receipt['etag'], 'article-tag')
        self.assertEqual(receipt['sha256'], hashlib.sha256(BODY).hexdigest())
        self.assertEqual(gzip.decompress((self.root / receipt['raw_path']).read_bytes()), BODY)
        rows = {row['イベントID']: row for row in self.reviews()}
        self.assertEqual(rows['rss:' + event['event_id']]['SHA256'], event['source_hash'])
        self.assertEqual(rows['rss:' + event['event_id']]['状態'], 'PENDING_REVIEW')
        self.assertEqual(rows['rss:article:' + event['event_id']]['SHA256'], receipt['sha256'])
        self.assertEqual(rows['rss:article:' + event['event_id']]['状態'], 'EVIDENCE_READY')
        self.run_watch(now=observed + timedelta(minutes=15), fetcher=self.getter())
        self.assertEqual(self.candidates(), [event])
        self.assertEqual(self.state()['press_rss']['article_evidence'][event['event_id']], receipt)
        self.assertEqual(self.requests, [URL])
        self.assertEqual(self.master.read_bytes(), b'unchanged master')

    def test_rejects_wrong_article_or_incomplete_response_and_retries(self):
        self.prepare()
        observed = NOW + timedelta(minutes=15)
        other = BODY.replace(b'SC/16506', b'SC/165060')
        response = (self.getter(other), self.getter(BODY, status=202, headers={'x-amzn-waf-action': 'challenge'}),
                    self.getter(BODY, response_url=URL + '?redirected=1'))
        for retry, getter in enumerate(response):
            with self.subTest(retry=retry):
                result = self.run_watch(now=observed + timedelta(minutes=15 * retry), fetcher=getter)
                event, = self.candidates()
                self.assertFalse(result['ok'])
                self.assertEqual(self.state()['press_rss']['status'], 'observed')
                self.assertFalse(self.state()['press_rss'].get('article_evidence'))
                self.assertFalse((self.root / f'data/raw/un/{hashlib.sha256(other).hexdigest()}.html.gz').exists())
                self.assertFalse((self.root / f'data/raw/un/{hashlib.sha256(BODY).hexdigest()}.html.gz').exists())
                self.assertTrue(any(row['イベントID'] == 'rss:article:' + event['event_id'] and
                                    row['状態'] == 'EVIDENCE_FAILED' for row in self.reviews()))
        self.run_watch(now=observed + timedelta(minutes=45), fetcher=self.getter())
        self.assertEqual(len(self.state()['press_rss']['article_evidence']), 1)
        receipt = next(iter(self.state()['press_rss']['article_evidence'].values()))
        (self.root / receipt['raw_path']).write_bytes(gzip.compress(b'corrupted'))
        result = self.run_watch(now=observed + timedelta(minutes=60), fetcher=self.getter())
        self.assertFalse(result['ok'])
        self.assertFalse(any(row['状態'] == 'EVIDENCE_READY' for row in self.reviews()))
        self.assertEqual(self.requests, [URL] * 4)

    def test_no_article_request_without_verified_rss_original(self):
        self.prepare()
        observed = NOW + timedelta(minutes=15)
        self.run_watch(now=observed, fetcher=self.getter(b'<html/>'))
        event, = self.candidates()
        (self.root / f'data/raw/un/{event["source_hash"]}.xml.gz').unlink()
        self.feed.rss = rss()
        self.run_watch(now=observed + timedelta(minutes=15), fetcher=self.getter())
        self.assertEqual(self.requests, [URL])
        self.assertTrue(any(row['状態'] == 'EVIDENCE_FAILED' for row in self.reviews()))


    def test_fails_explicitly_for_a_mismatched_rss_title_without_fetching(self):
        self.prepare()
        self.feed.rss = self.feed.rss.replace(b'Amends One Entity', b'Removes One Entity')
        result = self.run_watch(now=NOW + timedelta(minutes=15), fetcher=self.getter())
        self.assertFalse(result['ok'])
        self.assertEqual(self.requests, [])
        self.assertTrue(any(row['状態'] == 'EVIDENCE_FAILED' and 'RSS title' in row['対象名／発表']
                            for row in self.reviews()))

    def test_rechecks_an_archived_original_when_the_rss_head_changes(self):
        self.prepare()
        observed = NOW + timedelta(minutes=15)
        self.run_watch(now=observed, fetcher=self.getter())
        event, = self.candidates()
        receipt = self.state()['press_rss']['article_evidence'][event['event_id']]
        (self.root / receipt['raw_path']).unlink()
        self.feed.rss = self.feed.rss.replace(TITLE.encode(), b'Security Council Revises One Entry')
        self.run_watch(now=observed + timedelta(minutes=15), fetcher=self.getter())
        rows = {row['イベントID']: row for row in self.reviews()}
        _, revision = self.candidates()
        self.assertFalse(revision['record']['candidate'])
        self.assertEqual(rows['rss:article:' + event['event_id']]['状態'], 'EVIDENCE_FAILED')
        self.assertEqual(rows['rss:article:' + revision['event_id']]['状態'], 'EVIDENCE_FAILED')
        self.assertEqual(self.requests, [URL])

    def test_bounds_the_read_of_an_existing_content_addressed_article(self):
        self.prepare()
        raw = self.root / f'data/raw/un/{hashlib.sha256(BODY).hexdigest()}.html.gz'
        raw.write_bytes(gzip.compress(b'0' * (3 * 1024 * 1024)))
        result = self.run_watch(now=NOW + timedelta(minutes=15), fetcher=self.getter())
        self.assertFalse(result['ok'])
        self.assertTrue(any(row['状態'] == 'EVIDENCE_FAILED' and 'exceeds size limit' in row['対象名／発表']
                            for row in self.reviews()))


if __name__=='__main__':unittest.main()
