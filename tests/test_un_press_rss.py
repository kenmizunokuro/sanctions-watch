import copy
import importlib
import unittest
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

NOW=datetime(2026,10,27,1,tzinfo=timezone.utc)
URL='https://press.un.org/en/rss.xml'


def rss(items=None):
    items=items if items is not None else [('sc16499','Security Council Sanctions Committee Amends Entry')]
    rows=''.join('<item><title>'+escape(title)+'</title><link>https://press.un.org/en/2026/'+key+
                 '.doc.htm</link><guid>'+key+'</guid><pubDate>Tue, 27 Oct 2026 00:30:00 +0000</pubDate></item>'
                 for key,title in items)
    return ('<rss version="2.0"><channel><title>UN Meetings Coverage and Press Releases</title>'
            '<link>https://press.un.org/en</link>'+rows+'</channel></rss>').encode()


class PressRSSTests(unittest.TestCase):
    def setUp(self):self.P=importlib.import_module('src.un_press_rss')
    def parse(self,body=None):return self.P.parse(rss() if body is None else body,now=NOW)
    def observe(self,previous=None,items=None,now=NOW):
        return self.P.observe(previous or {},self.parse() if items is None else items,
                              source_hash='a'*64,now=now)

    def test_baseline_repetition_and_semantic_new_candidate(self):
        first=self.observe();self.assertEqual(first['new_candidates'],0)
        self.assertEqual(len(first['events']),1)
        self.assertEqual(first['events'][0]['status'],'BASELINE_RECORDED')
        second=self.P.observe(first,self.parse(),source_hash='b'*64,now=NOW+timedelta(minutes=15))
        self.assertEqual(second['events'],first['events'])
        self.assertEqual(second['new_candidates'],0)
        third=self.observe(second,self.parse(rss([('sc16499','Security Council Sanctions Committee Amends Entry'),
                                                 ('sc16500','Security Council Sanctions Committee Adds Entry')])),now=NOW+timedelta(minutes=30))
        self.assertEqual(third['new_candidates'],1)
        self.assertEqual(third['events'][-1]['status'],'PENDING_REVIEW')
        self.assertEqual(third['events'][-1]['detected_at'],'2026-10-27T01:30:00Z')

    def test_title_reversions_and_relevant_to_unrelated_revision_remain_distinct(self):
        state=self.observe();ids=[state['events'][-1]['event_id']]
        for title in ['Committee sanctions: revised','Ordinary Council news','Security Council Sanctions Committee Amends Entry']:
            state=self.observe(state,self.parse(rss([('sc16499',title)])))
            self.assertEqual(state['new_candidates'],1);ids.append(state['events'][-1]['event_id'])
        self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual(state['events'][1]['detected_at'],state['events'][0]['detected_at'])

    def test_unrelated_news_does_not_become_sanctions_candidate(self):
        items=self.parse(rss([('sgsm1','Secretary General calls for sanctions'),('sc1','Council meets on peacekeeping')]))
        self.assertFalse(any(x['candidate'] for x in items))
        self.assertEqual(self.observe(items=items)['events'],[])

    def test_added_and_revised_counts_are_separate_from_raw_feed_hash(self):
        first=self.observe()
        changed=self.observe(first,self.parse(rss([('sc16499','Revised sanctions announcement'),
                                                   ('sc16500','New sanctions announcement')])))
        self.assertEqual(changed['new_articles'],1)
        self.assertEqual(changed['revised_articles'],1)
        self.assertEqual(changed['new_candidates'],2)
        self.assertEqual(first['new_articles'],0)

    def test_gap_from_disjoint_page_or_long_interval_persists(self):
        first=self.observe()
        other=self.parse(rss([('sc16500','New sanctions announcement')]))
        gap=self.observe(first,other)
        self.assertTrue(gap['coverage_gap']);self.assertEqual(gap['status'],'coverage_gap')
        repeated=self.observe(gap,other);self.assertEqual(repeated['coverage_gap'],gap['coverage_gap'])
        delayed=self.observe(first,now=NOW+timedelta(minutes=91))
        self.assertTrue(delayed['coverage_gap'])

    def test_stale_content_and_future_publication_are_not_normal(self):
        stale=self.observe(now=NOW+timedelta(hours=73))
        self.assertEqual(stale['status'],'stale');self.assertEqual(stale['last_success_at'],'2026-10-30T02:00:00Z')
        with self.assertRaises(ValueError):self.parse(rss().replace(b'27 Oct',b'28 Oct'))

    def test_unsafe_malformed_duplicate_and_external_inputs_fail_closed(self):
        cases=[b'<html/>',b'<rss/>',rss([]),rss([('sc1','sanctions'),('sc1','sanctions again')]),
               rss().replace(b'https://press.un.org/en/2026/',b'https://example.com/en/2026/'),
               rss().replace(b'</guid>',b'</guid><guid>other</guid>'),
               rss().replace(b'+0000',b''),
               rss().replace(b'<link>https://press.un.org/en</link>',b'<link>https://example.com</link>')]
        for body in cases:
            with self.subTest(body=body[:90]),self.assertRaises(ValueError):self.parse(body)
        for enc in ['utf-8','utf-16','utf-16-le','utf-16-be']:
            text='<?xml version="1.0" encoding="'+('UTF-8' if enc=='utf-8' else 'UTF-16')+'"?><!DOCTYPE rss [<!ENTITY e "boom">]>'+rss().decode()
            with self.subTest(enc=enc),self.assertRaises(ValueError):self.parse(text.encode(enc))


if __name__=='__main__':unittest.main()
