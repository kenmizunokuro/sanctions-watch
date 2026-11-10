import copy,csv,gzip,hashlib,importlib,io,json,shutil,tempfile,unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
from src import master as M,integrity_legacy as L
from src.fetch import Fetched

ROOT=Path(__file__).resolve().parent.parent
NOW=datetime(2026,11,11,tzinfo=timezone.utc)
FOLDER='docs/evidence/mof-source-review-20261110'
APPROVAL='data/review/approved_mof_source_update_20261110.json'


class ApprovedMofSourceUpdateTests(unittest.TestCase):
    def setUp(self):
        try:self.A=importlib.import_module('src.approved_mof_source_update')
        except ImportError:self.fail('Approved MOF source update implementation is missing')
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        files=[APPROVAL,L.LEDGER_PATH,L.POLICY_PATH,L.SCOPE_PATH,'data/master/master.csv',
               'data/review/integrity_retention_decisions.json','data/review/ofac_party_removal_queue.csv',
               'data/state.json','data/source_record_state/mof.json','data/source_diff/mof_latest.csv',
               'data/dashboard/changes.csv','data/dashboard/mof_pending.csv','data/dashboard/re_review.csv',
               'data/diff/latest.csv','data/kubun_map.json']
        for rel in files:
            p=self.root/rel;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/rel,p)
        shutil.copytree(ROOT/FOLDER,self.root/FOLDER)
        state=json.loads((self.root/'data/source_record_state/mof.json').read_bytes())
        raw=state['processed_raw'];p=self.root/raw;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/raw,p)
        self.proposal=json.loads(gzip.decompress((self.root/FOLDER/'proposed-changes.json.gz').read_bytes()))
        # These fixed approval tests remain valid after production has applied it.
        rows=M.load(self.root/'data/master/master.csv')
        for c in self.proposal['cases']:
            if c['before'] is None:rows.pop(c['match_key'],None)
            else:rows[c['match_key']]=copy.deepcopy(c['before'])
        M.save(rows,self.root/'data/master/master.csv')
        p=self.root/'data/state.json';v=json.loads(p.read_bytes());v['mof']=self.proposal['source_state_before'];p.write_text(json.dumps(v))
        p=self.root/'data/source_record_state/mof.json'
        oldraw='data/raw/mof/20261020T120119Z__shisantouketsu20261021.csv.gz'
        target=self.root/oldraw;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/oldraw,target)
        p.write_text(json.dumps(dict(processed_raw=oldraw,processed_content_sha256=self.proposal['source_before_sha256'])))
        shutil.copyfile(ROOT/FOLDER/'reviewed-candidates.csv',self.root/'data/dashboard/mof_pending.csv')
        p=self.root/'data/review/integrity_retention_decisions.json';v=json.loads(p.read_bytes());ids={r['review_id'] for r in v['entries']}
        for e in self.proposal['retention_decisions_change']['withdrawn']:
            if e['prior_decision']['review_id'] not in ids:v['entries'].append(e['prior_decision'])
        p.write_text(json.dumps(v))
        for rel in ['data/dashboard/changes.csv','data/diff/latest.csv']:
            p=self.root/rel
            with p.open(newline='') as f:lines=list(csv.reader(f))
            with p.open('w',newline='') as f:csv.writer(f).writerows([lines[0],*[r for r in lines[1:] if 'MOF承認反映20261110' not in r[-1]]])
        self.before=M.load(self.root/'data/master/master.csv')
        self.source=Fetched(url=self.proposal['source_url'],body=gzip.decompress((self.root/FOLDER/'official-20261105.csv.gz').read_bytes()),
                            sha256=self.proposal['source_after_sha256'],http_status=200,etag='verified',last_modified='verified',filename='shisantouketsu20261105.csv')
        self.enterContext(patch.object(self.A,'current_source',return_value=self.source))
        self.enterContext(patch.object(self.A.I,'audit',return_value=dict(status='PASS',publication_allowed=True,blocking_counts={},counts={},findings=[])))

    def snapshot(self):return {str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
    def apply(self,**kwargs):return self.A.apply(self.root,now=NOW,run_url='https://github.com/example/repo/actions/runs/123',**kwargs)

    def test_exact_approved_generation_preserves_dates_and_distinguishes_quality_events(self):
        result=self.apply();self.assertEqual(result['status'],'APPLIED');self.assertEqual(result['count'],358)
        after=M.load(self.root/'data/master/master.csv');keys={c['match_key'] for c in self.proposal['cases']}
        self.assertEqual({k for k in after if after[k]!=self.before.get(k)},keys)
        for c in self.proposal['cases']:
            row=after[c['match_key']]
            self.assertEqual(row['first_seen_ms'],str(c['after']['first_seen_ms']))
            self.assertEqual(row['last_updated_ms'],str(int(NOW.timestamp()*1000)))
            self.assertEqual({k:v for k,v in row.items() if k!='last_updated_ms'},{k:str(v) for k,v in c['after'].items() if k!='last_updated_ms'})
        with (self.root/'data/dashboard/mof_pending.csv').open() as f:pending=list(csv.DictReader(f))
        self.assertEqual(pending,[])
        state=json.loads((self.root/'data/source_record_state/mof.json').read_bytes());self.assertEqual(state['processed_content_sha256'],self.source.sha256)
        with (self.root/'data/dashboard/changes.csv').open() as f:history=list(csv.DictReader(f))
        approved=[r for r in history if 'MOF承認反映20261110' in r['変更後']]
        self.assertEqual(len(approved),412)
        quality=[r for r in approved if 'Weak Alias参考情報化' in r['変更後']]
        self.assertEqual(len(quality),313);self.assertEqual({r['検知日時'] for r in quality},{'2026-11-10 14:51:06'})
        self.assertEqual({r['検知日時'] for r in approved if r not in quality},{'2026-11-05 05:00:03'})
        self.assertEqual(sum(r['種別']=='掲載終了' for r in approved),31)
        screened=list(csv.DictReader(io.StringIO(gzip.decompress((self.root/'data/dashboard/screening.csv.gz').read_bytes()).decode())))
        weak={c['match_key'] for c in self.proposal['cases'] if c['kind']=='WEAK_ALIAS_REFERENCE_ONLY'}
        self.assertFalse(weak & {r['match_key'] for r in screened})
        saved=self.snapshot();self.assertEqual(self.apply()['status'],'ALREADY_APPLIED');self.assertEqual(saved,self.snapshot())

    def test_changed_before_row_or_approval_or_source_state_has_no_partial_writes(self):
        for fault in ('row','approval','state','pending','source'):
            with self.subTest(fault=fault):
                saved=self.snapshot()
                if fault=='row':
                    rows=M.load(self.root/'data/master/master.csv');rows[next(c['match_key'] for c in self.proposal['cases'] if c['before'])]['risk_level']='changed';M.save(rows,self.root/'data/master/master.csv')
                elif fault=='approval':(self.root/APPROVAL).write_bytes((self.root/APPROVAL).read_bytes()+b' ')
                elif fault=='state':
                    p=self.root/'data/state.json';v=json.loads(p.read_bytes());v['mof']['sha256']='0'*64;p.write_text(json.dumps(v))
                elif fault=='pending':
                    p=self.root/'data/dashboard/mof_pending.csv'
                    with p.open(newline='') as f:v=list(csv.reader(f))
                    with p.open('w',newline='') as f:csv.writer(f).writerows(v[:-1])
                else:self.source.sha256='0'*64
                altered=self.snapshot()
                with self.assertRaises(ValueError):self.apply()
                self.assertEqual(altered,self.snapshot())
                for rel,b in saved.items():(self.root/rel).write_bytes(b)
                self.source.sha256=self.proposal['source_after_sha256']

    def test_audit_veto_or_replace_failure_rolls_back_the_whole_generation(self):
        saved=self.snapshot()
        with patch.object(self.A.I,'audit',return_value=dict(publication_allowed=False,blocking_counts={'UNDETERMINED':1})):
            with self.assertRaises(ValueError):self.apply()
        self.assertEqual(saved,self.snapshot())
        from src import persistence as P
        real=P.os.replace;failed=False
        def replace(a,b):
            nonlocal failed
            if Path(b)==self.root/'data/review/integrity_retention_decisions.json' and not failed:
                failed=True;raise OSError('interrupted publication after master replacement')
            return real(a,b)
        with patch.object(P.os,'replace',side_effect=replace):
            with self.assertRaises(OSError):self.apply()
        self.assertEqual(saved,self.snapshot())
        self.assertTrue(failed)

    def test_unrelated_fresh_main_change_is_preserved(self):
        rows=M.load(self.root/'data/master/master.csv');selected={c['match_key'] for c in self.proposal['cases']}
        key=next(k for k,r in rows.items() if k not in selected and not r['review_flag'])
        rows[key]['risk_level']='Unrelated latest-main change';M.save(rows,self.root/'data/master/master.csv')
        self.apply();self.assertEqual(M.load(self.root/'data/master/master.csv')[key],rows[key])

    def test_expired_approval_blocks_before_any_writes(self):
        saved=self.snapshot()
        with self.assertRaises(ValueError):self.A.apply(self.root,now=datetime(2026,11,26,tzinfo=timezone.utc))
        self.assertEqual(saved,self.snapshot())

    def test_second_source_check_veto_leaves_entire_generation_unchanged(self):
        for late in (Fetched(url=self.source.url,body=self.source.body,sha256='0'*64,http_status=200),
                     RuntimeError('source unavailable after audit')):
            with self.subTest(late=type(late).__name__):
                saved=self.snapshot()
                with patch.object(self.A,'current_source',side_effect=[self.source,late]):
                    with self.assertRaises((ValueError,RuntimeError)):self.apply()
                self.assertEqual(saved,self.snapshot())


class ApprovedMofDeliveryTests(unittest.TestCase):
    apply=ApprovedMofSourceUpdateTests.apply
    def setUp(self):
        ApprovedMofSourceUpdateTests.setUp(self)
        try:self.D=importlib.import_module('src.approved_mof_delivery')
        except ImportError:self.fail('Resumable approved MOF delivery implementation is missing')

    def _published(self):
        import subprocess
        def audit(stage,*args):
            rels=['data/master/master.csv','data/state.json','data/review/integrity_retention_decisions.json']
            return dict(publication_allowed=True,blocking_counts={},inputs={r:hashlib.sha256((stage/r).read_bytes()).hexdigest() for r in rels})
        with patch.object(self.A.I,'audit',side_effect=audit):self.apply()
        self.git=lambda *a:subprocess.check_output(['git','-C',str(self.root),*a],stderr=subprocess.DEVNULL)
        remote=Path(self.enterContext(tempfile.TemporaryDirectory()))/'origin.git'
        subprocess.run(['git','init','--bare',str(remote)],check=True,capture_output=True)
        self.git('init','-b','main');self.git('config','user.name','Tests');self.git('config','user.email','tests@example.invalid')
        self.git('add','data','docs');self.git('commit','-m','published approved generation');self.git('remote','add','origin',str(remote));self.git('push','-u','origin','main')

    def _resume_fault(self,fault):
        import subprocess
        self._published();failed=False
        def runner(cmd,**kwargs):
            nonlocal failed
            if len(cmd)>2 and cmd[1:3]==['-m','src.export_excel']:
                p=self.root/'dist/black_receiver_name_all.xlsx';p.parent.mkdir(exist_ok=True);p.write_bytes(b'full workbook');return subprocess.CompletedProcess(cmd,0)
            if len(cmd)>2 and cmd[1:3]==['-m','src.export_diff_excel']:
                p=Path(cmd[cmd.index('--out')+1]);p.parent.mkdir(exist_ok=True);p.write_bytes(b'import workbook');return subprocess.CompletedProcess(cmd,0)
            is_monitor=len(cmd)>2 and cmd[1:3]==['-m','src.watch']
            if ((cmd[0]=='gh' and fault=='release') or (is_monitor and fault=='monitor')) and not failed:
                failed=True;raise subprocess.CalledProcessError(1,cmd)
            if cmd[0]=='gh' or (len(cmd)>2 and cmd[1:3]==['-m','src.watch']):return subprocess.CompletedProcess(cmd,0)
            return subprocess.run(cmd,**kwargs)
        with self.assertRaises(subprocess.CalledProcessError):self.D.deliver(self.root,run=runner)
        self.assertFalse((self.root/self.D.DELIVERY).exists())
        self.assertEqual(self.apply()['status'],'ALREADY_APPLIED')
        self.assertTrue(self.D.delivery_needed(self.root))
        result=self.D.deliver(self.root,run=runner);self.assertEqual(result['status'],'DELIVERED')
        body=(self.root/self.D.DELIVERY).read_bytes();self.assertEqual(self.git('show','origin/main:'+str(self.D.DELIVERY)),body)
        self.assertFalse(self.D.delivery_needed(self.root));self.assertEqual(self.D.deliver(self.root,run=runner)['status'],'ALREADY_DELIVERED')

    def test_pushed_application_resumes_failed_release(self):self._resume_fault('release')
    def test_pushed_application_resumes_failed_live_monitor(self):self._resume_fault('monitor')

    def test_delivery_refuses_a_superseded_material_generation(self):
        self._published();p=self.root/'data/master/master.csv';p.write_bytes(p.read_bytes()+b'\n')
        with self.assertRaises(ValueError):self.D.deliver(self.root)
        self.assertFalse((self.root/self.D.DELIVERY).exists())


if __name__=='__main__':unittest.main()
