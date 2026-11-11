import copy
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from src import un_watch as W, un_review as R, un_sources as U, un_press_rss as P
from tests.test_un_watch import Feed, NOW
from tests.test_un_press_rss import rss


class RSSReviewEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.feed=Feed()
        W.run(self.root,fetcher=self.feed,now=NOW)
        self.baseline=W.load(self.root)

    def edit(self,fn):
        state=W.load(self.root);fn(state)
        (self.root/W.STATE_PATH).write_text(json.dumps(state))

    def candidate(self):
        return next(e for e in W.load(self.root)['press_rss']['events'] if e['status']=='PENDING_REVIEW')

    def create_candidate(self,revision=False):
        self.feed.rss=rss([('sc16499','Sanctions Committee revises its announcement')]) if revision else rss([
            ('sc16499','Security Council Sanctions Committee Amends Entry'),('sc16500','New sanctions announcement')])
        W.run(self.root,fetcher=self.feed,now=NOW+timedelta(minutes=15))

    def review(self):
        event=self.candidate()
        return R.review_rss_notice(self.root,event_id='rss:'+event['event_id'],index_hash=event['source_hash'],
            reviewer='Reviewer',when=NOW+timedelta(minutes=16),note='checked official original')

    def assert_rejected_without_writes(self,operation):
        before={str(p.relative_to(self.root)):p.read_bytes() for p in (self.root/'data').rglob('*') if p.is_file()}
        with self.assertRaises(ValueError):operation()
        after={str(p.relative_to(self.root)):p.read_bytes() for p in (self.root/'data').rglob('*') if p.is_file()}
        self.assertEqual(before,after)

    def test_fabricated_current_record_is_rejected_even_with_recomputed_event_id(self):
        self.create_candidate()
        def corrupt(state):
            event=next(e for e in state['press_rss']['events'] if e['status']=='PENDING_REVIEW')
            event['record']['title']='A title not present in the official RSS'
            event['event_id']=U.digest([event['parent_event_id'],event['record'],event['source_hash']])
        self.edit(corrupt);self.assert_rejected_without_writes(self.review)

    def test_event_identity_inconsistent_with_original_record_is_rejected(self):
        self.create_candidate()
        def corrupt(state):
            next(e for e in state['press_rss']['events'] if e['status']=='PENDING_REVIEW')['event_id']='e'*64
        self.edit(corrupt);self.assert_rejected_without_writes(self.review)

    def test_revision_previous_record_must_exist_in_previous_original(self):
        self.create_candidate(revision=True)
        def corrupt(state):
            event=next(e for e in state['press_rss']['events'] if e['status']=='PENDING_REVIEW')
            event['previous_source_hash']=self.baseline['documents']['press_rss']['sha256']
            event['previous_record']['title']='Fabricated previous title'
        self.edit(corrupt);self.assert_rejected_without_writes(self.review)

    def test_revision_records_previous_source_and_reviews_both_versions(self):
        self.create_candidate(revision=True)
        self.assertEqual(self.candidate()['previous_source_hash'],self.baseline['documents']['press_rss']['sha256'])
        result=self.review();self.assertEqual(result['status'],'REVIEWED')

    def test_legacy_revision_can_recover_exact_previous_evidence_from_parent(self):
        self.create_candidate(revision=True)
        self.edit(lambda s:next(e for e in s['press_rss']['events'] if e['status']=='PENDING_REVIEW').pop('previous_source_hash',None))
        result=self.review()
        self.assertEqual(result['previous_source_hash'],self.baseline['documents']['press_rss']['sha256'])

    def test_coverage_cannot_trust_fabricated_freshness_or_current_items(self):
        # A genuinely old original still hashes correctly. A corrupted cached date
        # must not authorize clearing the gap.
        self.feed.rss=rss().replace(b'Tue, 27 Oct 2026 00:30:00',b'Fri, 23 Oct 2026 00:30:00')
        W.run(self.root,fetcher=self.feed,now=NOW+timedelta(minutes=91))
        def corrupt(state):
            state['press_rss']['stale']=False
            state['press_rss']['latest_publication_at']='2026-10-27T02:30:00Z'
        self.edit(corrupt)
        source_hash=W.load(self.root)['documents']['press_rss']['sha256']
        self.assert_rejected_without_writes(lambda:R.review_rss_coverage(self.root,index_hash=source_hash,
            reviewer='Reviewer',when=NOW+timedelta(minutes=92),note='checked official archive'))

    def test_coverage_current_item_mismatch_cannot_be_cleared(self):
        W.run(self.root,fetcher=self.feed,now=NOW+timedelta(minutes=91))
        self.edit(lambda s:s['press_rss']['items'][0].update(title='Not in original'))
        source_hash=W.load(self.root)['documents']['press_rss']['sha256']
        self.assert_rejected_without_writes(lambda:R.review_rss_coverage(self.root,index_hash=source_hash,
            reviewer='Reviewer',when=NOW+timedelta(minutes=92),note='checked official archive'))

    def test_unrelated_to_relevant_revision_has_provable_prior_original(self):
        self.feed.rss=rss([('sc16499','Ordinary council news')])
        W.run(self.root,fetcher=self.feed,now=NOW+timedelta(minutes=1))
        prior_hash=W.load(self.root)['documents']['press_rss']['sha256']
        # Another URL was never a candidate, so there is no retained parent.
        self.feed.rss=rss([('sc16499','Ordinary council news'),('sc16501','Council meeting')])
        W.run(self.root,fetcher=self.feed,now=NOW+timedelta(minutes=2))
        prior_hash=W.load(self.root)['documents']['press_rss']['sha256']
        self.feed.rss=rss([('sc16499','Ordinary council news'),('sc16501','Council sanctions announcement')])
        W.run(self.root,fetcher=self.feed,now=NOW+timedelta(minutes=3))
        event=next(e for e in W.load(self.root)['press_rss']['events']
                   if e['status']=='PENDING_REVIEW' and e['record']['url'].endswith('sc16501.doc.htm'))
        self.assertEqual(event['parent_event_id'],'');self.assertEqual(event['previous_source_hash'],prior_hash)
        result=R.review_rss_notice(self.root,event_id=event['event_id'],index_hash=event['source_hash'],
            reviewer='Reviewer',when=NOW+timedelta(minutes=4),note='verified both originals')
        self.assertEqual(result['status'],'REVIEWED')

    def test_legacy_previous_record_without_exact_parent_remains_pending(self):
        self.create_candidate(revision=True)
        def corrupt(state):
            event=next(e for e in state['press_rss']['events'] if e['status']=='PENDING_REVIEW')
            event.pop('previous_source_hash',None)
            event['parent_event_id']=''
            event['event_id']=U.digest(['',event['record'],event['source_hash']])
        self.edit(corrupt);self.assert_rejected_without_writes(self.review)

    def test_missing_original_fails_with_controlled_error_and_no_writes(self):
        self.create_candidate();event=self.candidate()
        (self.root/f"data/raw/un/{event['source_hash']}.xml.gz").unlink()
        self.assert_rejected_without_writes(self.review)

    def test_valid_reversion_preserves_id_detection_and_master(self):
        self.create_candidate(revision=True);self.review()
        self.feed.rss=rss()
        W.run(self.root,fetcher=self.feed,now=NOW+timedelta(minutes=17))
        event=self.candidate();before=copy.deepcopy(event)
        result=R.review_rss_notice(self.root,event_id=event['event_id'],index_hash=event['source_hash'],
            reviewer='Reviewer',when=NOW+timedelta(minutes=18),note='official title reversion verified')
        self.assertEqual(result['event_id'],before['event_id'])
        self.assertEqual(result['detected_at'],before['detected_at'])
        self.assertFalse((self.root/'data/master/master.csv').exists())

    def test_older_genuine_version_cannot_replace_immediately_preceding_candidate(self):
        self.create_candidate(revision=True)
        self.feed.rss=rss([('sc16499','Ordinary council news')])
        W.run(self.root,fetcher=self.feed,now=NOW+timedelta(minutes=16))
        state=W.load(self.root);event=state['press_rss']['events'][-1]
        oldest=self.baseline['press_rss']['events'][0]
        event['previous_record']=copy.deepcopy(oldest['record'])
        event['previous_source_hash']=oldest['source_hash']
        (self.root/W.STATE_PATH).write_text(json.dumps(state))
        self.assert_rejected_without_writes(lambda:R.review_rss_notice(self.root,event_id=event['event_id'],
            index_hash=event['source_hash'],reviewer='Reviewer',when=NOW+timedelta(minutes=17),note='wrong prior version'))

    def test_identical_previous_and_current_record_is_not_a_revision(self):
        self.create_candidate(revision=True)
        def corrupt(state):
            event=next(e for e in state['press_rss']['events'] if e['status']=='PENDING_REVIEW')
            old=self.baseline['press_rss']['events'][0]
            event.update(record=copy.deepcopy(old['record']),previous_record=copy.deepcopy(old['record']),
                         source_hash=old['source_hash'],previous_source_hash=old['source_hash'])
            event['event_id']=U.digest([event['parent_event_id'],event['record'],event['source_hash']])
        self.edit(corrupt);self.assert_rejected_without_writes(self.review)

    def test_intervening_non_candidate_revisions_preserve_valid_prior_evidence(self):
        for minutes,title in [(1,'Ordinary council news'),(2,'Revised ordinary council news'),
                              (3,'Sanctions committee announces an amendment')]:
            self.feed.rss=rss([('sc16499',title)])
            W.run(self.root,fetcher=self.feed,now=NOW+timedelta(minutes=minutes))
        event=W.load(self.root)['press_rss']['events'][-1]
        self.assertEqual(event['previous_record']['title'],'Revised ordinary council news')
        result=R.review_rss_notice(self.root,event_id=event['event_id'],index_hash=event['source_hash'],
            reviewer='Reviewer',when=NOW+timedelta(minutes=4),note='intervening non-candidate version verified')
        self.assertEqual(result['status'],'REVIEWED')


if __name__=='__main__':unittest.main()
