import copy
import csv
import gzip
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src import integrity_audit as A
from tests.test_integrity_readers import mof_csv


def expected(name='Jane Doe', ident='1', primary=True, weak=False):
    return dict(source='OFAC', category='SDN', source_id=ident, name=name,
                primary=primary, weak=weak, source_url='https://official.test/list',
                source_hash='a'*64, raw_path='raw.xml', locator='Party:'+ident)


def master(name='Jane Doe', key='janedoe', source='OFAC', category='OFAC:SDN'):
    return dict(display_name=name,match_key=key,status='有効',sources=source,categories=category,
                variants=json.dumps([name]))


def index(name='Jane Doe', ident='1', **changes):
    row=dict(list='SDN',party_id=ident,name=name,match_key='janedoe',primary='1',low_quality='0',
             alias_current='1',party_current='1',formats='advanced_xml_v3')
    row.update(changes);return row


def kinds(result): return {f['kind'] for f in result['findings']}


class CompareTests(unittest.TestCase):
    def compare(self, es=None, ms=None, ix=None):
        return A.compare(es if es is not None else [expected()],
                         ms if ms is not None else [master()],
                         ix if ix is not None else [index()], {'SDN':'OFAC:SDN'})

    def test_intact_snapshot_passes_without_mutating_inputs(self):
        inputs=([expected()],[master()],[index()]);before=copy.deepcopy(inputs)
        result=self.compare(*inputs)
        self.assertEqual(result['status'],'PASS');self.assertEqual(inputs,before)
        self.assertEqual(result['lineage'][0]['source_id'],'1')

    def test_missing_primary_and_alias_have_different_kinds(self):
        self.assertIn('MISSING_REGISTRATION',kinds(self.compare(ms=[])))
        self.assertIn('MISSING_ALIAS',kinds(self.compare(es=[expected(primary=False)],ms=[])))

    def test_other_source_cannot_hide_missing_provenance(self):
        self.assertIn('SOURCE_MISMATCH',kinds(self.compare(ms=[master(source='財務省',category='財務省:分類')])))
        self.assertIn('SOURCE_MISMATCH',kinds(self.compare(ms=[master(category='OFAC:Consolidated')])))

    def test_inactive_primary_is_not_covered_by_name_only(self):
        row=master();row['status']='無効'
        self.assertIn('STATUS_MISMATCH',kinds(self.compare(ms=[row])))

    def test_wrong_party_id_and_orphan_current_index_are_detected(self):
        result=self.compare(ix=[index(ident='999')])
        self.assertEqual(result['status'],'BLOCKED')
        self.assertTrue({'MISSING_ALIAS_EVIDENCE','SOURCE_ID_MISMATCH'} <= kinds(result))

    def test_same_name_for_two_parties_requires_both_evidence_links(self):
        result=self.compare(es=[expected(),expected(ident='2')])
        self.assertIn('MISSING_ALIAS_EVIDENCE',kinds(result))
        self.assertEqual(self.compare(es=[expected(),expected(ident='2')],ix=[index(),index(ident='2')])['status'],'PASS')

    def test_weak_alias_is_required_in_index_but_not_master(self):
        e=expected(weak=True,primary=False)
        ix=index(primary='0',low_quality='1')
        self.assertEqual(self.compare(es=[e],ms=[],ix=[ix])['status'],'PASS')
        self.assertIn('WEAK_ALIAS_ACTIVE',kinds(self.compare(es=[e],ix=[ix])))
        self.assertIn('ALIAS_QUALITY_MISMATCH',kinds(self.compare(es=[e],ms=[])))

    def test_strong_on_other_party_overrides_weak_master_exclusion_only(self):
        es=[expected(weak=True,primary=False),expected(ident='2')]
        self.assertEqual(self.compare(es=es,ix=[index(primary='0',low_quality='1'),index(ident='2')])['status'],'PASS')

    def test_classic_primary_role_is_not_xml_alias_primary_flag(self):
        e=expected();e['format']='classic_csv'
        self.assertEqual(self.compare(es=[e],ix=[index(primary='0')])['status'],'PASS')

    def test_weak_alias_with_other_source_does_not_force_inactivation(self):
        self.assertEqual(self.compare(es=[expected(weak=True,primary=False)],
                                     ms=[master(source='OFAC;財務省')],
                                     ix=[index(primary='0',low_quality='1')])['status'],'PASS')

    def test_current_party_without_current_alias_is_not_silently_accepted(self):
        self.assertIn('SOURCE_ID_MISMATCH',kinds(self.compare(ix=[index(),index(ident='999',alias_current='0')])) )

    def test_unsupported_category_is_detected_but_other_source_categories_are_preserved(self):
        for category in ['OFAC:SDN;OFAC:Consolidated','OFAC:SDN;OFAC:UNKNOWN']:
            with self.subTest(category=category):
                self.assertIn('SOURCE_MISMATCH',kinds(self.compare(ms=[master(category=category)])))
        self.assertEqual(self.compare(ms=[master(source='OFAC;経産省',category='OFAC:SDN;経産省:手動')])['status'],'PASS')
        es=[expected(),dict(expected(ident='2'),category='Consolidated')]
        ix=[index(),dict(index(ident='2'),list='Consolidated')]
        result=A.compare(es,[master(category='OFAC:SDN;OFAC:Consolidated')],ix,
                         {'SDN':'OFAC:SDN','Consolidated':'OFAC:Consolidated'})
        self.assertEqual(result['status'],'PASS')

    def test_retained_delisted_registration_is_reviewed_never_deleted(self):
        es=[expected(name='Another Name',ident='2')]
        ms=[master(),master('Another Name','anothername')]
        ix=[index(alias_current='0',party_current='0'),index('Another Name','2',match_key='anothername')]
        result=self.compare(es,ms,ix)
        self.assertIn('EXTRA_REGISTRATION',kinds(result))
        self.assertEqual(len(ms),2)

    def test_unsupported_generated_display_and_duplicate_rows_block(self):
        row=master('Invented Person')
        self.assertIn('MISGENERATED',kinds(self.compare(ms=[row])))
        self.assertIn('DUPLICATE_RECORD',kinds(self.compare(ms=[master(),master()])))

    def test_mof_ids_and_four_regression_names_are_not_fuzzy_matched(self):
        from src.integrity_readers import read_mof
        es=read_mof(Path('tests/fixtures/integrity/mof_four_names.csv').read_bytes())
        result=A.compare(es,[],[],{'9':'財務省:コンゴ民主共和国(個人)'})
        self.assertEqual(result['status'],'BLOCKED')
        for name in ['SEBASTIEN UWIMBABAZI','MUHAMMED LUMISA','LUMISA, MUHAMAD','CHARLES SEMATAMA']:
            f=next(f for f in result['findings'] if f.get('official',{}).get('name')==name)
            self.assertEqual(f['kind'],'MISSING_ALIAS' if name=='LUMISA, MUHAMAD' else 'MISSING_REGISTRATION')


class AuditIOTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.body=mof_csv()
        path=self.root/'data/raw/mof/test.csv.gz';path.parent.mkdir(parents=True);path.write_bytes(gzip.compress(self.body))
        state={'mof':{'sha256':hashlib.sha256(self.body).hexdigest(),'url':'https://www.mof.go.jp/test.csv'}}
        (self.root/'data/state.json').write_text(json.dumps(state))
        (self.root/'data/kubun_map.json').write_text(json.dumps({'map':{'9':'コンゴ民主共和国(個人)'}}))
        self.mpath=self.root/'data/master/master.csv';self.mpath.parent.mkdir(parents=True)
        row=master('MUHAMMED LUMISA','muhammedlumisa','財務省','財務省:コンゴ民主共和国(個人)')
        with self.mpath.open('w') as f:
            w=csv.DictWriter(f,fieldnames=list(row));w.writeheader();w.writerow(row)

    def test_audit_is_deterministic_offline_and_preserves_business_data(self):
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        one=A.audit(self.root,['mof']);two=A.audit(self.root,['mof'])
        self.assertEqual(one,two);self.assertEqual(one['status'],'PASS')
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        path=A.write_report(self.root,one);path2=A.write_report(self.root,one)
        self.assertNotEqual(path,path2)
        self.assertEqual(json.loads(gzip.decompress(path.read_bytes())),one)

    def test_corrupt_or_missing_cached_body_blocks_even_when_unchanged(self):
        path=next(self.root.glob('data/raw/mof/*'));path.write_bytes(gzip.compress(b'wrong'))
        result=A.audit(self.root,['mof'])
        self.assertEqual(result['status'],'BLOCKED');self.assertIn('UNDETERMINED',kinds(result))
        path.unlink()
        self.assertEqual(A.audit(self.root,['mof'])['status'],'BLOCKED')

    def test_parse_failure_is_a_finding_with_hash_and_url(self):
        path=next(self.root.glob('data/raw/mof/*'));body=b'not a csv'
        path.write_bytes(gzip.compress(body));sp=self.root/'data/state.json'
        state=json.loads(sp.read_text());state['mof']['sha256']=hashlib.sha256(body).hexdigest();sp.write_text(json.dumps(state))
        result=A.audit(self.root,['mof']);self.assertEqual(result['status'],'BLOCKED')
        self.assertEqual(result['documents'][0]['sha256'],hashlib.sha256(body).hexdigest())
        self.assertIn('https://www.mof.go.jp/test.csv',json.dumps(result))

    def test_upstream_failure_cannot_be_reported_as_pass_using_old_snapshot(self):
        result=A.audit(self.root,['mof'],upstream_failed=True)
        self.assertEqual(result['status'],'BLOCKED');self.assertIn('UPSTREAM_FAILED',kinds(result))

    def test_unrecognized_state_shape_still_produces_blocked_report(self):
        (self.root/'data/state.json').write_text('[]')
        result=A.audit(self.root,['mof'])
        self.assertEqual(result['status'],'BLOCKED')
        self.assertIn('UNDETERMINED',kinds(result))

    def test_corrupt_deflate_body_still_produces_failure_evidence(self):
        # Valid gzip framing with invalid deflate bytes raises zlib.error,
        # not BadGzipFile/OSError. This must still be a recorded veto.
        path=next(self.root.glob('data/raw/mof/*'))
        path.write_bytes(b'\x1f\x8b\x08\x00'+b'\x00'*6+b'\xff'*30)
        result=A.audit(self.root,['mof'])
        self.assertEqual(result['status'],'BLOCKED');self.assertIn('UNDETERMINED',kinds(result))
        self.assertTrue(A.write_report(self.root,result).exists())

    def test_cli_blocks_returns_nonzero_and_writes_evidence(self):
        self.mpath.unlink()
        p=subprocess.run([sys.executable,'-m','src.integrity_audit','--root',str(self.root),'--sources','mof'],capture_output=True,text=True)
        self.assertNotEqual(p.returncode,0)
        reports=list(self.root.glob('data/integrity_audit/*.json.gz'))
        self.assertEqual(len(reports),1)
        self.assertEqual(json.loads(gzip.decompress(reports[0].read_bytes()))['status'],'BLOCKED')


if __name__=='__main__': unittest.main()
