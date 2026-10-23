import copy
import csv
import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from src import integrity_audit as A
from src.normalize import match_key
from tests import test_integrity_audit as F


class CategoryAndKeyTests(unittest.TestCase):
    def test_blank_label_is_not_a_missing_master_category(self):
        e=dict(F.expected(),source='財務省',category='35')
        m=F.master(source='財務省',category='')
        r=A.compare([e],[m],[],{'35':None})
        self.assertEqual(r['status'],'REVIEW')
        self.assertTrue(r['publication_allowed'])
        self.assertEqual([f['kind'] for f in r['findings']],['CATEGORY_LABEL_UNRESOLVED'])
        self.assertFalse(r['findings'][0]['blocking'])
        self.assertEqual(r['lineage'][0]['category'],'35')

    def test_absent_mapping_is_still_blocking(self):
        e=dict(F.expected(),source='財務省',category='35')
        r=A.compare([e],[F.master(source='財務省',category='')],[],{})
        self.assertEqual(r['status'],'BLOCKED')
        self.assertFalse(r['publication_allowed'])

    def test_unresolved_label_cannot_hide_source_or_name_or_status_failure(self):
        e=dict(F.expected(),source='財務省',category='35')
        for rows in [[],[F.master()], [dict(F.master(source='財務省',category=''),status='無効')]]:
            with self.subTest(rows=rows):
                self.assertEqual(A.compare([e],rows,[],{'35':None})['status'],'BLOCKED')

    def test_outer_quotes_are_removed_before_surname_order(self):
        name='CHERNOMORSKI BANK RAZVITIYA I REKONSTRUKTSII, OTKRYTOE AKTSIONERNOE OBSHCHESTVO'
        self.assertEqual(match_key("'"+name+"'"),match_key(name))
        self.assertEqual(match_key("O'BRIEN, Sean"),"seano'brien")




class TriageTests(unittest.TestCase):
    def module(self):
        from src import integrity_triage as T
        return T

    def witness(self,name='Jane Doe',ident='1',current=False,field='name',transform='exact'):
        return dict(name=name,source_id=ident,category='SDN',current=current,field=field,
                    transform=transform,source_url='https://official.test/raw',source_hash='a'*64,
                    raw_path='data/raw/old.csv.gz',locator='line:1',weak=False,primary=True)

    def report(self,kind='EXTRA_REGISTRATION'):
        return dict(schema_version=1,status='BLOCKED',publication_allowed=False,inputs={},documents=[],
                    findings=[dict(kind=kind,detail='review',blocking=True,checked_source='OFAC',master=F.master())])

    def test_retired_history_requires_matching_original_party_evidence(self):
        T=self.module();ix=[F.index(alias_current='0',party_current='0')]
        r=T.classify(self.report(),[F.master()],ix,{'janedoe':[self.witness()]})
        item=r['items'][0]
        self.assertEqual(item['classification'],'HISTORICAL_PARTY_CONFIRMED')
        self.assertTrue(item['retention_eligible'])
        self.assertFalse(item['approved'])
        wrong=T.classify(self.report(),[F.master()],ix,{'janedoe':[self.witness(ident='999')]})
        self.assertEqual(wrong['items'][0]['classification'],'HISTORY_WITHOUT_ORIGINAL')
        self.assertFalse(wrong['items'][0]['retention_eligible'])

    def test_non_name_field_is_not_a_prior_official_name(self):
        T=self.module();r=T.classify(self.report(),[F.master()],[],
                                   {'janedoe':[self.witness(field='call_sign',current=True)]})
        self.assertEqual(r['items'][0]['classification'],'NON_NAME_FIELD_ONLY')
        self.assertFalse(r['items'][0]['retention_eligible'])

    def test_current_comma_variant_is_explicit_and_does_not_approve_itself(self):
        T=self.module();w=self.witness(name='DOE, Jane',current=True,transform='comma_removed')
        r=T.classify(self.report(),[F.master()],[],{'janedoe':[w]})
        self.assertEqual(r['items'][0]['classification'],'CURRENT_FORMAT_VARIANT')
        self.assertTrue(r['items'][0]['retention_eligible']);self.assertFalse(r['items'][0]['approved'])

    def test_official_only_findings_keep_names_and_distinct_record_ids(self):
        T=self.module()
        report=dict(self.report(),findings=[dict(kind='CATEGORY_LABEL_UNRESOLVED',blocking=False,
            official=dict(F.expected(name=name,ident=ident),source='財務省',category='35'))
            for name,ident in [('Jane Doe','35-1'),('John Doe','35-2')]])
        items=T.classify(report,[],[],{})['items']
        self.assertEqual([x['display_name'] for x in items],['Jane Doe','John Doe'])
        self.assertNotEqual(items[0]['review_id'],items[1]['review_id'])

    def test_ofac_history_cannot_approve_an_extra_mof_source(self):
        T=self.module();report=self.report()
        report['findings'][0]['checked_source']='財務省'
        item=T.classify(report,[F.master()],[],{'janedoe':[self.witness()]})['items'][0]
        self.assertFalse(item['retention_eligible'])

    def test_engine_and_approval_keep_dual_source_findings_separate(self):
        from src import integrity_decisions as D
        from datetime import datetime,timezone
        T=self.module()
        es=[F.expected(),dict(F.expected('Current MOF','2'),source='財務省',category='9')]
        rows=[F.master(),F.master('Current MOF','currentmof','財務省','財務省:分類'),
              F.master('Retired Person','retiredperson','OFAC;財務省','OFAC:SDN;財務省:分類')]
        index=[F.index(),F.index('Retired Person','3',match_key='retiredperson',alias_current='0',party_current='0')]
        report=A.compare(es,rows,index,{'SDN':'OFAC:SDN','9':'財務省:分類'})
        extras=[f for f in report['findings'] if f['kind']=='EXTRA_REGISTRATION']
        self.assertEqual({f['checked_source'] for f in extras},{'OFAC','財務省'})
        triage=T.classify(report,rows,index,{'retiredperson':[self.witness('Retired Person','3')]})
        eligible=[x for x in triage['items'] if x['retention_eligible']]
        self.assertEqual(len(eligible),1);item=eligible[0]
        decision=dict(review_id=item['review_id'],decision='APPROVED_RETAIN',reviewed_by='test reviewer',
                      reviewed_at='2026-01-01T00:00:00Z',expires_at='2030-01-01T00:00:00Z',reason='OFAC only',
                      reviewed_evidence_sha256=item['evidence_sha256'])
        result=A._finish(D.apply(report,triage,{'schema_version':1,'entries':[decision]},
                                 now=datetime(2026,10,23,tzinfo=timezone.utc)))
        self.assertEqual(result['status'],'BLOCKED')
        remaining=[f['checked_source'] for f in result['findings'] if f.get('blocking',True)]
        self.assertEqual(remaining,['財務省'])

    def test_source_less_old_extra_finding_cannot_be_approved(self):
        report=self.report();report['findings'][0].pop('checked_source',None)
        item=self.module().classify(report,[F.master()],[],{'janedoe':[self.witness()]})['items'][0]
        self.assertFalse(item['retention_eligible'])

    def test_distinct_source_claims_have_distinct_review_ids(self):
        report=self.report();one=report['findings'][0];one['checked_source']='OFAC'
        two=dict(one,checked_source='財務省');report['findings'].append(two)
        items=self.module().classify(report,[F.master()],[],{'janedoe':[self.witness()]})['items']
        self.assertNotEqual(items[0]['review_id'],items[1]['review_id'])

    def test_missing_evidence_is_not_intentional_retention(self):
        r=self.module().classify(self.report(),[F.master()],[],{})
        self.assertEqual(r['items'][0]['classification'],'ORIGIN_UNRESOLVED')
        self.assertFalse(r['items'][0]['retention_eligible'])

    def test_duplicate_key_repair_is_proposal_and_preserves_both_rows(self):
        T=self.module();bad=dict(F.master(),match_key='oldbadkey');before=copy.deepcopy(bad)
        report=self.report('MISGENERATED');report['findings'][0]['master']=bad
        rows=[bad,F.master()]
        item=T.classify(report,rows,[],{})['items'][0]
        self.assertEqual(item['classification'],'KEY_COLLISION_REVIEW')
        self.assertEqual(item['repair_proposal']['target_key'],'janedoe')
        self.assertEqual(item['repair_proposal']['action'],'REVIEW_MERGE')
        self.assertEqual(bad,before);self.assertEqual(len(rows),2)
        self.assertFalse(item['retention_eligible'])

    def test_new_defect_is_never_a_retention_candidate(self):
        for kind in ['MISSING_ALIAS','SOURCE_ID_MISMATCH','UNDETERMINED']:
            report=self.report(kind)
            self.assertFalse(self.module().classify(report,[F.master()],[],{})['items'][0]['retention_eligible'])

    def test_triage_refuses_stale_report_or_tampered_inputs(self):
        T=self.module()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'data').mkdir();(root/'data/state.json').write_text('{}')
            report=dict(self.report(),inputs={'data/state.json':'0'*64})
            with self.assertRaises(T.TriageError):T.triage(root,report)

    def test_review_identity_binds_row_and_party_not_unrelated_document_updates(self):
        T=self.module();ix=[F.index(alias_current='0',party_current='0')]
        report=self.report();proof=self.witness()
        one=T.classify(report,[F.master()],ix,{'janedoe':[proof]})['items'][0]
        proof2=dict(proof,source_hash='b'*64)
        two=T.classify(report,[F.master()],ix,{'janedoe':[proof2]})['items'][0]
        self.assertEqual(one['review_id'],two['review_id'])
        changed=copy.deepcopy(report);changed['findings'][0]['master']['sources']='OFAC;財務省'
        three=T.classify(changed,[changed['findings'][0]['master']],ix,{'janedoe':[proof]})['items'][0]
        self.assertNotEqual(one['review_id'],three['review_id'])


class DecisionTests(unittest.TestCase):
    def setUp(self):
        from datetime import datetime,timezone
        from src import integrity_triage as T
        self.now=datetime(2026,10,23,tzinfo=timezone.utc)
        helper=TriageTests();self.report=helper.report()
        self.triage=T.classify(self.report,[F.master()],[F.index(alias_current='0',party_current='0')],
                               {'janedoe':[helper.witness()]})
        self.item=self.triage['items'][0]
        self.entry=dict(review_id=self.item['review_id'],decision='APPROVED_RETAIN',
                        reviewed_by='named reviewer',reviewed_at='2026-10-22T00:00:00Z',
                        expires_at='2026-11-22T00:00:00Z',reason='Keep the specifically reviewed historical name',
                        reviewed_evidence_sha256=self.item['evidence_sha256'])

    def apply(self,entries=None,report=None,triage=None):
        from src import integrity_decisions as D
        return D.apply(report or self.report,triage or self.triage,
                       {'schema_version':1,'entries':entries if entries is not None else [self.entry]},now=self.now)

    def test_only_explicit_valid_approval_releases_that_exact_retention_finding(self):
        r=self.apply()
        self.assertFalse(r['findings'][0]['blocking']);self.assertTrue(r['findings'][0]['retention_approved'])
        self.assertTrue(self.report['findings'][0]['blocking'])
        self.assertTrue(self.apply(entries=[])['findings'][0]['blocking'])

    def test_expired_or_future_review_does_not_release(self):
        for changes in [{'expires_at':'2026-10-22T12:00:00Z'},{'reviewed_at':'2026-10-24T00:00:00Z'}]:
            self.assertTrue(self.apply(entries=[dict(self.entry,**changes)])['findings'][0]['blocking'])

    def test_changed_master_or_party_relationship_invalidates_approval(self):
        from src import integrity_triage as T
        helper=TriageTests();changed=copy.deepcopy(self.report)
        changed['findings'][0]['master']['categories']='OFAC:Consolidated'
        triage=T.classify(changed,[changed['findings'][0]['master']],
                          [F.index(alias_current='0',party_current='0')],{'janedoe':[helper.witness()]})
        self.assertTrue(self.apply(report=changed,triage=triage)['findings'][0]['blocking'])
        triage2=T.classify(self.report,[F.master()],[F.index(ident='999',alias_current='0',party_current='0')],
                           {'janedoe':[helper.witness(ident='999')]})
        self.assertTrue(self.apply(triage=triage2)['findings'][0]['blocking'])

    def test_missing_names_and_id_or_parse_failures_cannot_be_approved(self):
        from src import integrity_triage as T
        for kind in ['MISSING_REGISTRATION','MISSING_ALIAS','SOURCE_ID_MISMATCH','UNDETERMINED','MISGENERATED']:
            report=copy.deepcopy(self.report);report['findings'][0]['kind']=kind
            triage=T.classify(report,[F.master()],[],{})
            entry=dict(self.entry,review_id=triage['items'][0]['review_id'])
            self.assertTrue(self.apply(entries=[entry],report=report,triage=triage)['findings'][0]['blocking'])

    def test_duplicate_or_malformed_decision_is_not_silently_ignored(self):
        from src import integrity_decisions as D
        for entries in [[self.entry,self.entry],[dict(self.entry,reviewed_by='')],[dict(self.entry,review_id='*')]]:
            with self.assertRaises(D.DecisionError):self.apply(entries=entries)


class CliIntegrationTests(unittest.TestCase):
    def setUp(self):
        from tests import test_integrity_audit as fixtures
        fixtures.AuditIOTests.setUp(self)

    def test_cli_preserves_review_status_without_blocking_blank_category_label(self):
        import subprocess,sys
        path=self.root/'data/kubun_map.json';path.write_text(json.dumps({'map':{'9':''}}))
        with self.mpath.open() as stream:
            reader=csv.DictReader(stream);header=reader.fieldnames;rows=list(reader)
        rows[0]['categories']=''
        with self.mpath.open('w') as stream:
            writer=csv.DictWriter(stream,fieldnames=header);writer.writeheader();writer.writerows(rows)
        p=subprocess.run([sys.executable,'-m','src.integrity_audit','--root',str(self.root),'--sources','mof','--triage'],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertIn('REVIEW',p.stdout)
        self.assertTrue(list(self.root.glob('data/integrity_audit/*triage*.csv')))

    def test_repeated_cli_triage_keeps_audit_contents_reproducible(self):
        import contextlib,io
        with contextlib.redirect_stdout(io.StringIO()):
            A.main(['--root',str(self.root),'--sources','mof','--triage'])
            A.main(['--root',str(self.root),'--sources','mof','--triage'])
        paths=sorted(p for p in self.root.glob('data/integrity_audit/*.json.gz') if not p.name.startswith('triage-'))
        self.assertEqual(len(paths),2)
        self.assertEqual(json.loads(gzip.decompress(paths[0].read_bytes())),json.loads(gzip.decompress(paths[1].read_bytes())))

    def test_invalid_decision_file_blocks_even_otherwise_healthy_candidate(self):
        (self.root/'data/review').mkdir();(self.root/'data/review/integrity_retention_decisions.json').write_text('{invalid')
        result=A.audit(self.root,['mof'])
        self.assertEqual(result['status'],'BLOCKED')
        self.assertIn('RETENTION_DECISION_ERROR',{f['kind'] for f in result['findings']})


class NoticeTests(unittest.TestCase):
    def test_notice_reader_uses_only_official_body_and_boundary_matches(self):
        from src import integrity_triage as T
        body=b'<html><nav><p>Jane Doe</p></nav><div class="field--name-field-body"><p>JOHN DOE; A JANE DOE TEST</p></div></html>'
        self.assertEqual(T.notice_mentions(body,{'John Doe','Doe','Jane','Jane Doe','Jo'}),
                         {'John Doe','Doe','Jane','Jane Doe'})
        self.assertNotIn('Jane Doe',T.notice_mentions(b'<nav>Jane Doe</nav><div class="field--name-field-body">John Doe</div>',{'Jane Doe'}))
        with self.assertRaises(T.TriageError):T.notice_mentions(b'<nav>Jane Doe</nav>',{'Jane Doe'})

    def test_notice_mention_is_not_proof_of_prior_name_or_delisting(self):
        from src import integrity_triage as T
        helper=TriageTests();w=helper.witness(field='notice_text')
        item=T.classify(helper.report(),[F.master()],[],{'janedoe':[w]})['items'][0]
        self.assertEqual(item['classification'],'NOTICE_MENTION_ONLY')
        self.assertFalse(item['retention_eligible'])


class RetentionGateTests(unittest.TestCase):
    def setUp(self):
        from tests import test_integrity_publication as G
        from src import integrity_triage as T
        G.GateTests.setUp(self);G.GateTests.install_ofac(self)
        self.run_gate=lambda: G.GateTests.run_gate(self,'success','ofac')
        with self.mpath.open() as stream:
            reader=csv.DictReader(stream);header=reader.fieldnames;rows=list(reader)
        rows.append(F.master('Retired Person','retiredperson'))
        with self.mpath.open('w') as stream:
            writer=csv.DictWriter(stream,fieldnames=header);writer.writeheader();writer.writerows(rows)
        path=self.root/'data/master/ofac_alias_history.csv'
        with path.open() as stream:
            reader=csv.DictReader(stream);header=reader.fieldnames;rows=list(reader)
        rows.append(F.index('Retired Person','999',match_key='retiredperson',alias_current='0',party_current='0'))
        with path.open('w') as stream:
            writer=csv.DictWriter(stream,fieldnames=header);writer.writeheader();writer.writerows(rows)
        path=self.root/'data/raw/ofac_sdn/20200101T000000Z__sdn.csv.gz'
        path.write_bytes(gzip.compress(b'999,"Retired Person",individual,SDGT,-0-,-0-,-0-,-0-,-0-,-0-,-0-,-0-\n'))
        report=A.audit(self.root,['ofac']);triage=T.triage(self.root,report)
        self.assertEqual(len(report['findings']),1)
        item=triage['items'][0];self.assertTrue(item['retention_eligible'])
        (self.root/'data/review').mkdir(exist_ok=True)
        (self.root/'data/review/integrity_retention_decisions.json').write_text(json.dumps({'schema_version':1,'entries':[
            dict(review_id=item['review_id'],decision='APPROVED_RETAIN',reviewed_by='test reviewer',
                 reviewed_at='2026-01-01T00:00:00Z',expires_at='2030-01-01T00:00:00Z',reason='Specific historical retention',
                 reviewed_evidence_sha256=item['evidence_sha256'])]}))

    def test_actual_gate_accepts_only_the_approved_retention_without_changing_data(self):
        before=self.mpath.read_bytes();p=self.run_gate()
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertIn('REVIEW',p.stdout);self.assertTrue((self.root/'published').exists())
        self.assertEqual(before,self.mpath.read_bytes())

    def test_bad_approval_date_still_writes_audit_failure_evidence(self):
        path=self.root/'data/review/integrity_retention_decisions.json'
        ledger=json.loads(path.read_text());ledger['entries'][0]['reviewed_at']='2026-01-01Z'
        ledger['entries'][0]['expires_at']='2030-01-01Z';path.write_text(json.dumps(ledger))
        p=self.run_gate();self.assertNotEqual(p.returncode,0)
        files=[p for p in self.root.glob('data/integrity_audit/*.json.gz') if not p.name.startswith('triage-')]
        self.assertEqual(len(files),1,p.stderr)
        report=json.loads(gzip.decompress(files[0].read_bytes()))
        self.assertIn('RETENTION_DECISION_ERROR',report['counts'])

    def test_classification_artifacts_show_applied_approval(self):
        p=self.run_gate();self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        report=json.loads(gzip.decompress(next(self.root.glob('data/integrity_audit/triage-*.json.gz')).read_bytes()))
        self.assertEqual(report['approved_count'],1)
        self.assertTrue(report['items'][0]['approved'])
        with next(self.root.glob('data/integrity_audit/triage-*.csv')).open(encoding='utf-8-sig') as stream:
            self.assertEqual(next(csv.DictReader(stream))['approved'],'True')

    def test_prior_retention_approval_does_not_hide_a_new_missing_alias(self):
        with self.mpath.open() as stream:
            reader=csv.DictReader(stream);header=reader.fieldnames;rows=list(reader)
        with self.mpath.open('w') as stream:
            writer=csv.DictWriter(stream,fieldnames=header);writer.writeheader()
            writer.writerows(r for r in rows if r['display_name']!='Jane Other')
        p=self.run_gate();self.assertNotEqual(p.returncode,0)
        self.assertFalse((self.root/'published').exists());self.assertIn('MISSING_ALIAS',p.stdout)

    def test_prior_retention_approval_does_not_hide_changed_raw_hash(self):
        path=self.root/'data/raw/ofac_sdn/prim.gz';path.write_bytes(gzip.compress(b'changed'))
        p=self.run_gate();self.assertNotEqual(p.returncode,0)
        self.assertFalse((self.root/'published').exists());self.assertIn('UNDETERMINED',p.stdout)

if __name__=='__main__': unittest.main()
