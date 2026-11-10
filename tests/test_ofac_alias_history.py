"""The approved alias keeps history without granting another name a waiver."""
import copy,csv,gzip,hashlib,importlib,io,json,os,shutil,subprocess,sys,tempfile,unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
from src import integrity_decisions as D,integrity_triage as T,master as M,integrity_legacy as L
ROOT=Path(__file__).resolve().parent.parent
APPROVAL='data/review/approved_volta_alias_history_20261110.json'
NOW=datetime(2026,11,11,tzinfo=timezone.utc)
KEY='voltashippingservicesfzco'

class AliasFixture(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.bundle=json.loads((ROOT/APPROVAL).read_bytes());b=self.bundle
        for rel in (APPROVAL,b['notice']['raw_path'],b['historical']['raw_path']):
            p=self.root/rel;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/rel,p)
        self.other=dict(b['before'],match_key='other',display_name='OTHER',variants='["OTHER"]',sources='UK',categories='UK:Test')
        M.save({KEY:b['before'],'other':self.other},self.root/'data/master/master.csv')
        p=self.root/'data/review/integrity_retention_decisions.json';p.write_text('{"schema_version":1,"entries":[]}')
        p=self.root/'data/dashboard/changes.csv';p.parent.mkdir(parents=True);p.write_text('検知日時,出所,種別,受取人名,変更前,変更後\n2026-11-01 00:00:00,UK,追加,OTHER,,old\n')
        p=self.root/'data/diff/latest.csv';p.parent.mkdir(parents=True);p.write_text('出所,種別,受取人名,変更前,変更後\nUK,追加,OTHER,,old\n')
        self.primary=[copy.deepcopy(b['current_verification']['primary_record'])]
        self.alias=[['58138','999','a.k.a.','VOLTA CONTAINER LINE FZCO','-0-']]
        ns='https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ADVANCED_XML'
        self.xml=f'<Sanctions xmlns="{ns}" Version="3"><DistinctParties><DistinctParty FixedRef="58138"><Profile><Identity ID="1" False="false"><Alias Primary="true" LowQuality="false"><DocumentedName ID="1"><DocumentedNamePart><NamePartValue ScriptID="1">VOLTA SHIPPING SERVICES LLC</NamePartValue></DocumentedNamePart></DocumentedName></Alias><Alias Primary="false" LowQuality="false"><DocumentedName ID="2"><DocumentedNamePart><NamePartValue ScriptID="1">VOLTA CONTAINER LINE FZCO</NamePartValue></DocumentedNamePart></DocumentedName></Alias></Identity></Profile></DistinctParty></DistinctParties></Sanctions>'.encode()
        self.report=dict(status='BLOCKED',inputs={},documents=[],findings=[dict(kind='EXTRA_REGISTRATION',checked_source='OFAC',master=b['after'],blocking=True)])
        self.write_current()
        self.ledger=dict(schema_version=1,entries=[copy.deepcopy(b['retention_decision'])])
    def write_current(self):
        self.report['documents']=[]
        for role,body in [('primary',self.csvbody(self.primary)),('alias',self.csvbody(self.alias)),('advanced',self.xml)]:
            folder='ofac_sdn_advanced' if role=='advanced' else 'ofac_sdn'
            path=self.root/f'data/raw/{folder}/current-{role}.csv';path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(body)
            url={'primary':'https://sanctionslistservice.ofac.treas.gov/api/download/SDN.CSV','alias':'https://sanctionslistservice.ofac.treas.gov/api/download/ALT.CSV','advanced':'https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.XML'}[role]
            self.report['documents'].append(dict(source='ofac_sdn',role=role,source_url=url,raw_path=path.relative_to(self.root).as_posix(),sha256=hashlib.sha256(body).hexdigest()))
    def csvbody(self,rows):
        s=io.StringIO();csv.writer(s).writerows(rows);return s.getvalue().encode()
    def result(self,now=NOW):
        b=self.bundle;M.save({KEY:b['after'],'other':self.other},self.root/'data/master/master.csv');t=T.classify(self.report,[b['after']],b['reviewed_index_links'],{KEY:b['reviewed_evidence']})
        return D.apply(self.report,t,self.ledger,now=now,root=self.root)
    def module(self):return importlib.import_module('src.ofac_alias_history')
    def snapshot(self):return {p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

class AliasEvidenceTests(AliasFixture):
    def test_source_refresh_keeps_independent_history_annotation(self):
        after=self.bundle['after'];self.assertEqual(M._recompute(copy.deepcopy(after)),after)
        row=dict(after,remark='制裁リスト（OFAC：SDN）')
        with self.assertRaises(ValueError):M._recompute(row)
    def test_only_exact_alias_is_released_and_unrelated_blocker_stays(self):
        self.report['findings'].append(dict(kind='UPSTREAM_FAILED',blocking=True))
        r=self.result();self.assertEqual([f['blocking'] for f in r['findings']],[False,True])
        self.assertEqual(r['retention_review'][0]['alias_history']['party_id'],'58138')
    def test_party_name_registration_or_replacement_alias_change_keeps_veto(self):
        primary=copy.deepcopy(self.primary);alias=copy.deepcopy(self.alias);xml=self.xml
        for mutation in ('party','name','registration','alias_party','alias_name','xml_weak','xml_party','missing_doc','hash','url'):
            with self.subTest(mutation=mutation):
                self.primary=copy.deepcopy(primary);self.alias=copy.deepcopy(alias);self.xml=xml
                if mutation=='party':self.primary[0][0]='999'
                if mutation=='name':self.primary[0][1]='OTHER'
                if mutation=='registration':self.primary[0][11]='Registration Number 0000000'
                if mutation=='alias_party':self.alias[0][0]='999'
                if mutation=='alias_name':self.alias[0][3]='OTHER'
                if mutation=='xml_weak':self.xml=xml.replace(b'Primary="false" LowQuality="false"',b'Primary="false" LowQuality="true"')
                if mutation=='xml_party':self.xml=xml.replace(b'FixedRef="58138"',b'FixedRef="999"')
                self.write_current()
                if mutation=='missing_doc':self.report['documents'].pop()
                if mutation=='hash':self.report['documents'][0]['sha256']='0'*64
                if mutation=='url':self.report['documents'][0]['source_url']='https://example.test/fake'
                self.assertTrue(self.result()['findings'][0]['blocking'])
    def test_tampered_approval_notice_or_original_cannot_release(self):
        for rel in (APPROVAL,self.bundle['notice']['raw_path'],self.bundle['historical']['raw_path']):
            p=self.root/rel;original=p.read_bytes();p.write_bytes(b'bad')
            self.assertTrue(self.result()['findings'][0]['blocking']);p.write_bytes(original)
    def test_missing_link_expiry_and_other_master_row_keep_veto(self):
        decision=self.ledger['entries'][0]
        for link in (None,{},'missing'):
            self.ledger['entries'][0]=copy.deepcopy(decision)
            if link=='missing':self.ledger['entries'][0].pop('alias_history')
            else:self.ledger['entries'][0]['alias_history']=link
            self.assertTrue(self.result()['findings'][0]['blocking'])
        self.ledger['entries'][0]=decision
        self.assertTrue(self.result(datetime(2026,11,26,tzinfo=timezone.utc))['findings'][0]['blocking'])
        self.report['findings'][0]['master']=dict(self.bundle['after'],display_name='OTHER',match_key='other')
        self.assertTrue(self.result()['findings'][0]['blocking'])

class AliasApplicationTests(AliasFixture):
    def test_atomic_single_row_annotation_exports_history_and_idempotence(self):
        A=self.module();receipt=A.apply(self.root,now=NOW)
        rows=M.load(self.root/'data/master/master.csv')
        self.assertEqual(rows[KEY],self.bundle['after']);self.assertEqual(rows['other'],self.other)
        self.assertNotIn('voltacontainerlinefzco',rows)
        for f in ('first_seen_ms','last_updated_ms'):self.assertEqual(rows[KEY][f],self.bundle['before'][f])
        for rel in ('data/dashboard/list.csv','data/dashboard/screening.csv.gz'):
            body=(self.root/rel).read_bytes();body=gzip.decompress(body) if rel.endswith('.gz') else body
            self.assertIn('制裁リスト（OFAC旧別名）',body.decode())
        history=list(csv.reader(io.StringIO((self.root/'data/dashboard/changes.csv').read_text(),newline='')))
        self.assertEqual(len(history),3);self.assertEqual(history[1][1:4],['OFAC','変更','VOLTA SHIPPING SERVICES FZCO'])
        latest=list(csv.reader(io.StringIO((self.root/'data/diff/latest.csv').read_text(),newline='')))
        self.assertIn(['UK','追加','OTHER','','old'],latest)
        self.assertEqual(receipt['count'],1)
        saved=self.snapshot();self.assertEqual(A.apply(self.root,now=NOW)['status'],'ALREADY_APPLIED');self.assertEqual(saved,self.snapshot())
    def test_changed_row_expired_approval_or_corrupt_original_writes_nothing(self):
        A=self.module();saved=self.snapshot()
        with self.assertRaises(ValueError):A.apply(self.root,now=datetime(2026,11,26,tzinfo=timezone.utc))
        self.assertEqual(saved,self.snapshot())
        p=self.root/'data/master/master.csv';rows=M.load(p);rows[KEY]['risk_level']='低';M.save(rows,p);saved=self.snapshot()
        with self.assertRaises(ValueError):A.apply(self.root,now=NOW)
        self.assertEqual(saved,self.snapshot())
    def test_replacement_failure_rolls_back_every_output(self):
        A=self.module();saved=self.snapshot()
        from src import persistence as P
        original=P.os.replace;count=0
        def fail(src,dst):
            nonlocal count
            count+=1
            if count==3:raise OSError('injected partial publication')
            return original(src,dst)
        with patch.object(P.os,'replace',side_effect=fail):
            with self.assertRaises(OSError):A.apply(self.root,now=NOW)
        self.assertEqual(self.snapshot(),saved)

class AliasPublisherTests(AliasFixture):
    def git(self,*args):return subprocess.run(['git',*args],cwd=self.root,check=True,capture_output=True,text=True).stdout.strip()
    def run_script(self):
        env=dict(os.environ,GITHUB_REF='refs/heads/main',PYTHONPATH='',PATH=str(self.root/'test-bin')+os.pathsep+os.environ['PATH'])
        return subprocess.run(['bash','.github/scripts/apply-approved-volta-alias-history.sh'],cwd=self.root,env=env,capture_output=True,text=True)
    def test_real_publish_checks_committed_and_remote_journal_before_skipping(self):
        A=self.module();shutil.copytree(ROOT/'src',self.root/'src',ignore=shutil.ignore_patterns('__pycache__'))
        scripts=self.root/'.github/scripts';scripts.mkdir(parents=True);shutil.copyfile(ROOT/'.github/scripts/apply-approved-volta-alias-history.sh',scripts/'apply-approved-volta-alias-history.sh')
        shim=self.root/'test-bin/python';shim.parent.mkdir();shim.write_text('#!'+sys.executable+'\n'+"""import os,sys
if sys.argv[1:3]==['-m','src.ofac_alias_history']:
    sys.path.insert(0,os.getcwd())
    from datetime import datetime,timezone
    from unittest.mock import patch
    from src import ofac_alias_history as A
    class FixedClock(datetime):
        @classmethod
        def now(cls,tz=None):return datetime(2026,11,11,tzinfo=timezone.utc)
    with patch.object(A,'datetime',FixedClock):raise SystemExit(A.main(sys.argv[3:]))
os.execv("""+repr(sys.executable)+""",["""+repr(sys.executable)+""",*sys.argv[1:]])
""");shim.chmod(0o755)
        (self.root/'.gitignore').write_text('__pycache__/\n')
        remote=Path(self.enterContext(tempfile.TemporaryDirectory()))/'origin.git';subprocess.run(['git','init','--bare',str(remote)],check=True,capture_output=True)
        self.git('init','-b','main');self.git('config','user.name','Test');self.git('config','user.email','test@example.invalid');self.git('add','.');self.git('commit','-m','baseline');self.git('remote','add','origin',str(remote));self.git('push','-u','origin','main')
        r=self.run_script();self.assertEqual(r.returncode,0,r.stderr);first=self.git('rev-parse','HEAD');self.assertEqual(first,self.git('rev-parse','origin/main'))
        r=self.run_script();self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(first,self.git('rev-parse','HEAD'))
        path=self.root/A.RECEIPT;body=path.read_bytes();path.write_bytes(body+b' ');self.assertNotEqual(self.run_script().returncode,0)
        path.write_bytes(body);receipt=json.loads(body);receipt['applied_at']='2026-11-11T01:00:00Z';path.write_text(json.dumps(receipt));self.git('add',A.RECEIPT.as_posix());self.git('commit','-m','unpublished receipt')
        self.assertNotEqual(self.run_script().returncode,0)

class MarkedAliasLifecycleTests(AliasFixture):
    def marked(self):
        M.save({KEY:self.bundle['after'],'other':self.other},self.root/'data/master/master.csv')
    def apply_without_finding(self,now=NOW):
        self.marked();report=dict(self.report,findings=[],scope=['ofac'],publication_allowed=True)
        return D.apply(report,T.classify(report,[],[],{}),self.ledger,now=now,root=self.root)
    def test_reappearing_old_alias_is_reviewed_even_without_extra_finding(self):
        from src import integrity_audit as A
        b=self.bundle;self.marked();self.alias.append(['58138','1000','a.k.a.',b['old_name'],'-0-']);self.write_current()
        index=dict(b['reviewed_index_links'][0],alias_current='1')
        expected=dict(source='OFAC',category='SDN',source_id='58138',name=index['name'],primary=False,weak=False)
        report=A.compare([expected],[b['after']],[index],{'SDN':'OFAC:SDN'})
        self.assertEqual(report['findings'],[])
        report.update(documents=self.report['documents'],scope=['ofac'])
        r=D.apply(report,T.classify(report,[b['after']],[index],{}),self.ledger,now=NOW,root=self.root)
        self.assertFalse(r['publication_allowed'])
        self.assertEqual(r['findings'][0]['kind'],'ALIAS_HISTORY_EVIDENCE_INVALID')
    def test_missing_current_evidence_expiry_and_missing_decision_still_block_without_findings(self):
        self.report['documents']=[]
        self.assertFalse(self.apply_without_finding()['publication_allowed'])
        self.write_current()
        self.assertFalse(self.apply_without_finding(datetime(2026,11,26,tzinfo=timezone.utc))['publication_allowed'])
        self.ledger['entries']=[]
        self.assertFalse(self.apply_without_finding()['publication_allowed'])
