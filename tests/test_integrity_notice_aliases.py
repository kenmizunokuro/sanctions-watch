"""Explicit historical alias fields never grant identity or retention approval."""
import csv
import gzip
import hashlib
import importlib
import json
import unittest
from pathlib import Path

from tests.test_integrity_notice_context import html
from tests import test_integrity_audit as F


class ReaderTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('src.integrity_notice_aliases'),'alias-field reader missing')
        return importlib.import_module('src.integrity_notice_aliases')

    def test_explicit_aka_fka_fields_preserve_quoted_parentheses_semicolons_and_positions(self):
        body=html('<h3>SDN</h3><h4>Changes:</h4><p>MAIN NAME (a.k.a. LAST, Jane; '
                  'f.k.a. "ALPHA; BETA (OLD)"; a.k.a. COMPANY (OFFSHORE)), Address; [TAG].</p>')
        result=self.module().read_notice_aliases(body)
        self.assertEqual([(r['alias_marker'],r['name']) for r in result],
                         [('a.k.a.','LAST, Jane'),('f.k.a.','"ALPHA; BETA (OLD)"'),('a.k.a.','COMPANY (OFFSHORE)')])
        for r in result:
            self.assertEqual(r['record'][r['alias_start']:r['alias_end']],r['name'])
            self.assertTrue(r['record'][r['group_start']:r['group_end']].startswith('(a.k.a.'))
            self.assertEqual(r['heading'],'Changes:')
            self.assertNotIn('party_id',r);self.assertNotIn('weak',r)

    def test_related_target_without_alias_marker_is_not_a_named_alias(self):
        self.assertEqual(self.module().read_notice_aliases(html('<p>VELES, Cyprus [TAG] (Linked To: Jane Doe).</p>')),[])

    def test_nested_related_alias_and_unbalanced_unknown_or_empty_fields_reject_whole_body(self):
        M=self.module()
        for text in ['MAIN (a.k.a. GOOD; d.b.a. UNKNOWN)', 'MAIN (a.k.a. GOOD; a.k.a. )',
                     'MAIN (a.k.a. "GOOD)', 'MAIN (a.k.a. GOOD', 'MAIN a.k.a. GOOD',
                     'MAIN (Linked To: OTHER (a.k.a. GOOD))', 'MAIN (a.k.a. GOOD; BAD)',
                     'MAIN (a.k.a GOOD)', 'MAIN (a.k.a. GOOD))']:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):M.read_notice_aliases(html('<p>FIRST (a.k.a. SAFE)</p><p>'+text+'</p>'))

    def test_unknown_later_markers_and_related_prefixes_reject_all_fields(self):
        M=self.module()
        for text in ['MAIN (a.k.a. GOOD), (d.b.a. UNKNOWN)', 'MAIN (n.k.a. GOOD)',
                     'MAIN; Related To: OTHER (a.k.a. GOOD)', 'MAIN; Linked To: OTHER (a.k.a. GOOD)']:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):M.read_notice_aliases(html('<p>'+text+'</p>'))

    def test_supported_typographic_quotes_keep_delimiters_inside_name_and_malformed_quotes_fail(self):
        M=self.module()
        result=M.read_notice_aliases(html('<p>MAIN (a.k.a. “ALPHA; BETA (OLD)”; f.k.a. «ONE; TWO»)</p>'))
        self.assertEqual([r['name'] for r in result],['“ALPHA; BETA (OLD)”','«ONE; TWO»'])
        for text in ['MAIN (a.k.a. “Jane Doe)', 'MAIN (a.k.a. Jane Doe”)', 'MAIN (a.k.a. «Jane Doe)',
                     'MAIN (a.k.a. “Jane Doe")']:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):M.read_notice_aliases(html('<p>'+text+'</p>'))

    def test_closed_linked_relationship_does_not_taint_later_change_side_alias_group(self):
        M=self.module()
        body=html('<p>FIRST (a.k.a. OLD), Address (Linked To: OTHER). -to- FIRST (a.k.a. NEW), B [TAG].</p>')
        try:result=M.read_notice_aliases(body)
        except ValueError:self.fail('closed relationship tainted a later alias group')
        self.assertEqual([r['name'] for r in result],['OLD','NEW'])

    def test_distinct_records_and_repeated_same_alias_keep_all_contexts(self):
        result=self.module().read_notice_aliases(html('<p>FIRST (a.k.a. SAME), A</p><p>SECOND (a.k.a. SAME), B</p>'))
        self.assertEqual(len(result),2);self.assertNotEqual(result[0]['record'],result[1]['record'])
        self.assertEqual([r['record_number'] for r in result],[1,2])


class IntegrationTests(unittest.TestCase):
    def module(self):return ReaderTests.module(self)

    def test_surname_order_alias_is_historical_ineligible_evidence_without_data_changes(self):
        self.module()
        from src import integrity_triage as T
        from tests.test_integrity_triage import TriageTests
        helper=TriageTests();report=helper.report();master=F.master('Jane LAST','janelast')
        report['findings'][0]['master']=master
        with __import__('tempfile').TemporaryDirectory() as temp:
            root=Path(temp);directory=root/'docs/evidence/integrity-notices';directory.mkdir(parents=True)
            body=html('<p>MAIN NAME (a.k.a. LAST, Jane), Address [TAG] (Linked To: Jane Different).</p>')
            path=directory/'notice.html.gz';path.write_bytes(gzip.compress(body,mtime=0))
            (directory/'manifest.json').write_text(json.dumps([dict(raw_path=path.relative_to(root).as_posix(),source_url='https://ofac.treasury.gov/recent-actions/20250630',source_hash=hashlib.sha256(body).hexdigest())]))
            before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
            witnesses={};manifest=[];T.add_notice_witnesses(root,[master],witnesses,manifest)
            self.assertIn('janelast',witnesses)
            proof=next(p for p in witnesses['janelast'] if p['field']=='notice_alias_name')
            self.assertFalse(proof['current']);self.assertIsNone(proof['source_id']);self.assertIsNone(proof['weak'])
            self.assertEqual(proof['name'],'LAST, Jane');self.assertEqual(proof['alias_marker'],'a.k.a.')
            item=T.classify(report,[master],[],witnesses)['items'][0]
            self.assertEqual(item['classification'],'NOTICE_ALIAS_FIELD_REVIEW')
            self.assertFalse(item['retention_eligible']);self.assertFalse(item['approved'])
            self.assertEqual(before,{p:p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_alias_context_does_not_override_current_or_verified_past_proof(self):
        self.module()
        from src import integrity_triage as T
        from tests.test_integrity_triage import TriageTests
        h=TriageTests();r=h.report();notice=dict(h.witness(),field='notice_alias_name',source_id=None,weak=None,
                                               alias_marker='a.k.a.',notice_record_sha256='a'*64)
        past=h.witness()
        item=T.classify(r,[F.master()],[],{'janedoe':[past,notice]})['items'][0]
        self.assertEqual(item['classification'],'ARCHIVED_NAME_WITHOUT_INDEX')

    def test_record_relationship_changes_review_id_and_never_approves_notice_only(self):
        self.module()
        from src import integrity_triage as T
        from src import integrity_decisions as D
        from tests.test_integrity_triage import TriageTests
        from datetime import datetime,timezone
        h=TriageTests();r=h.report();proof=dict(h.witness(),field='notice_alias_name',source_id=None,weak=None,
                                              alias_marker='a.k.a.',notice_record_sha256='a'*64)
        one=T.classify(r,[F.master()],[],{'janedoe':[proof]})
        two=T.classify(r,[F.master()],[],{'janedoe':[dict(proof,notice_record_sha256='b'*64)]})
        self.assertNotEqual(one['items'][0]['review_id'],two['items'][0]['review_id'])
        item=one['items'][0];entry=dict(review_id=item['review_id'],decision='APPROVED_RETAIN',reviewed_by='test reviewer',reviewed_at='2026-01-01T00:00:00Z',expires_at='2030-01-01T00:00:00Z',reason='Cannot approve notice alias',reviewed_evidence_sha256=item['evidence_sha256'])
        result=D.apply(r,one,{'schema_version':1,'entries':[entry]},now=datetime(2026,10,26,tzinfo=timezone.utc))
        self.assertTrue(result['findings'][0].get('blocking',True))


class PublicationTests(unittest.TestCase):
    def test_malformed_notice_alias_vetoes_actual_gate_and_preserves_failure_evidence(self):
        ReaderTests.module(self)
        from tests.test_integrity_publication import GateTests
        from src.integrity_audit import audit
        for text in ['MAIN (a.k.a. \"Jane Doe)', 'MAIN (a.k.a. Jane Doe), (d.b.a. UNKNOWN)',
                     'MAIN (n.k.a. Jane Doe)', 'MAIN; Related To: OTHER (a.k.a. Jane Doe)',
                     'MAIN (a.k.a. “Jane Doe)']:
          with self.subTest(text=text):
            gate=GateTests();gate.setUp();self.addCleanup(gate.doCleanups)
            self.assertEqual(audit(gate.root,['ofac','mof'])['status'],'PASS')
            directory=gate.root/'docs/evidence/integrity-notices';directory.mkdir(parents=True)
            body=html('<p>'+text+'</p>');path=directory/'notice.html.gz';path.write_bytes(gzip.compress(body,mtime=0))
            (directory/'manifest.json').write_text(json.dumps([dict(raw_path=path.relative_to(gate.root).as_posix(),source_url='https://ofac.treasury.gov/recent-actions/20250630',source_hash=hashlib.sha256(body).hexdigest())]))
            before=gate.mpath.read_bytes();result=gate.run_gate('success','mof')
            self.assertNotEqual(result.returncode,0);self.assertFalse((gate.root/'published').exists())
            self.assertEqual(before,gate.mpath.read_bytes())
            reports=[p for p in gate.root.glob('data/integrity_audit/*.json.gz') if not p.name.startswith('triage-')]
            self.assertEqual(len(reports),1,result.stderr)
            saved=json.loads(gzip.decompress(reports[0].read_bytes()));self.assertIn('TRIAGE_FAILED',saved['counts'])


if __name__=='__main__':unittest.main()
