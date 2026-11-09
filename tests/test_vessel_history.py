"""Three historical strings only; independent notice + current vessel evidence."""
import copy,csv,gzip,hashlib,io,json,shutil,tempfile,unittest
from datetime import datetime,timezone
from pathlib import Path
from src import integrity_decisions as D,integrity_triage as T,master as M

ROOT=Path(__file__).resolve().parent.parent
APPROVAL='data/review/approved_vessel_history_20261109.json'
NOW=datetime(2026,11,10,tzinfo=timezone.utc)
class VesselHistoryTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.bundle=json.loads((ROOT/APPROVAL).read_bytes());b=self.bundle
        for rel in (APPROVAL,b['notice']['raw_path'],*[d['raw_path'] for d in b['historical'].values()]):
            p=self.root/rel;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/rel,p)
        self.path=self.root/'data/raw/ofac_sdn/current.csv';self.path.parent.mkdir(parents=True)
        self.current=copy.deepcopy(b['current_verification']['records'])
        self.report=dict(status='BLOCKED',inputs={},documents=[dict(source='ofac_sdn',role='primary',source_url=b['current_verification']['source_url'],raw_path=self.path.relative_to(self.root).as_posix())],findings=[dict(kind='EXTRA_REGISTRATION',checked_source='OFAC',master=c['after'],blocking=True) for c in b['cases']])
        self.write_current()
        self.ledger=dict(schema_version=1,entries=[c['retention_decision'] for c in b['cases']])
    def write_current(self):
        with self.path.open('w',newline='') as f:csv.writer(f).writerows(self.current)
        self.report['documents'][0]['sha256']=hashlib.sha256(self.path.read_bytes()).hexdigest()
    def apply(self,now=NOW):
        links=[p for c in self.bundle['cases'] for p in c['reviewed_index_links']]
        proofs={c['master_key']:c['reviewed_evidence'] for c in self.bundle['cases']}
        t=T.classify(self.report,[c['after'] for c in self.bundle['cases']],links,proofs)
        return D.apply(self.report,t,self.ledger,now=now,root=self.root)
    def test_three_release_with_exact_vessel_proofs_and_upstream_veto_remains(self):
        self.report['findings'].append(dict(kind='UPSTREAM_FAILED',blocking=True))
        result=self.apply()
        self.assertEqual([f['blocking'] for f in result['findings']],[False,False,False,True])
        self.assertTrue(self.report['findings'][0]['blocking'])
        self.assertEqual({r['vessel_history']['imo'] for r in result['retention_review']},{'9502453','9259692','9289166'})
    def test_missing_or_malformed_vessel_link_cannot_bypass_identity_checks(self):
        for value in (None, '', {}, 'missing'):
            with self.subTest(value=value):
                self.ledger['entries']=copy.deepcopy([c['retention_decision'] for c in self.bundle['cases']])
                for entry in self.ledger['entries']:
                    if value=='missing':entry.pop('vessel_history')
                    else:entry['vessel_history']=value
                result=self.apply()
                self.assertTrue(all(f['blocking'] for f in result['findings']))
                self.assertTrue(all(r['status']=='EVIDENCE_INVALID' for r in result['retention_review']))

    def test_current_party_name_type_or_imo_change_blocks(self):
        original=copy.deepcopy(self.current)
        for column,value in ((0,'999999'),(1,'UNREVIEWED'),(2,'individual'),(11,'Vessel Registration Identification IMO 0000000')):
            with self.subTest(column=column):
                self.current=copy.deepcopy(original);self.current[0][column]=value;self.write_current()
                result=self.apply();self.assertTrue(result['findings'][0]['blocking'])
                self.assertEqual(result['retention_review'][0]['status'],'EVIDENCE_INVALID')
    def test_corrupt_notice_archives_or_current_bytes_fail_closed(self):
        for rel in (self.bundle['notice']['raw_path'],*[d['raw_path'] for d in self.bundle['historical'].values()]):
            with self.subTest(rel=rel):
                p=self.root/rel;original=p.read_bytes();p.write_bytes(b'broken')
                self.assertTrue(all(f['blocking'] for f in self.apply()['findings']));p.write_bytes(original)
        self.path.write_bytes(b'changed')
        self.assertTrue(all(f['blocking'] for f in self.apply()['findings']))
    def test_expiry_or_stale_master_does_not_release(self):
        self.assertTrue(all(f['blocking'] for f in self.apply(datetime(2026,11,26,tzinfo=timezone.utc))['findings']))
        self.report['findings'][0]['master']=dict(self.report['findings'][0]['master'],risk_level='低')
        self.assertTrue(self.apply()['findings'][0]['blocking'])
    def test_source_refresh_preserves_history_and_missing_annotation_blocks(self):
        for c in self.bundle['cases']:
            self.assertEqual(M._recompute(copy.deepcopy(c['after'])),c['after'])
        row=copy.deepcopy(self.bundle['cases'][0]['after']);row['remark']=row['before'] if 'before' in row else ''
        with self.assertRaises(ValueError):M._recompute(row)
