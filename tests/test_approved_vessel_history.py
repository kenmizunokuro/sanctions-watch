import copy,csv,gzip,hashlib,io,json,os,shutil,subprocess,sys,tempfile,unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
from src import approved_vessel_history as A, vessel_history as V, master as M, integrity_legacy as L
from src.mof_legacy_review import _read_rows
ROOT=Path(__file__).resolve().parent.parent
NOW=datetime(2026,11,10,tzinfo=timezone.utc)
class VesselFixture(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.bundle=V.approval(ROOT)
        for rel in (V.APPROVAL,L.POLICY_PATH,L.SCOPE_PATH,L.LEDGER_PATH,A.LEDGER,'data/master/master.csv','data/dashboard/changes.csv','data/diff/latest.csv','data/review/ofac_party_removal_queue.csv',self.bundle['notice']['raw_path'],*[d['raw_path'] for d in self.bundle['historical'].values()]):
            p=self.root/rel;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/rel,p)
        rows=_read_rows((self.root/'data/master/master.csv').read_text())
        for c in self.bundle['cases']:rows[c['master_key']]=c['before']
        M.save(rows,self.root/'data/master/master.csv')
        self.ledger=json.loads((self.root/A.LEDGER).read_bytes());ids={c['retention_decision']['review_id'] for c in self.bundle['cases']}
        self.ledger['entries']=[e for e in self.ledger['entries'] if e['review_id'] not in ids]
        (self.root/A.LEDGER).write_text(json.dumps(self.ledger,ensure_ascii=False,indent=2)+'\n')
    def snapshot(self):return {p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

class ApprovedVesselHistoryTests(VesselFixture):
    def test_exact_three_annotations_exports_events_and_idempotence(self):
        before=_read_rows((self.root/'data/master/master.csv').read_text());old_diff=(self.root/'data/diff/latest.csv').read_text()
        receipt=A.apply(self.root,now=NOW);after=_read_rows((self.root/'data/master/master.csv').read_text())
        changed={k for k in before if before[k]!=after[k]};self.assertEqual(changed,V.KEYS)
        for c in self.bundle['cases']:
            row=after[c['master_key']];self.assertEqual(row,c['after']);self.assertEqual(row['status'],'有効')
            for field in ['first_seen_ms','last_updated_ms','risk_level']:self.assertEqual(row[field],before[c['master_key']][field])
        ledger=json.loads((self.root/A.LEDGER).read_bytes());self.assertEqual(ledger['entries'][:len(self.ledger['entries'])],self.ledger['entries']);self.assertEqual(len(ledger['entries']),len(self.ledger['entries'])+3)
        for output in ('data/dashboard/list.csv','data/dashboard/screening.csv.gz'):
            raw=(self.root/output).read_bytes();raw=gzip.decompress(raw) if output.endswith('.gz') else raw
            for c in self.bundle['cases']:self.assertIn(c['old_name'],raw.decode())
        history=list(csv.DictReader(io.StringIO((self.root/'data/dashboard/changes.csv').read_text())))
        events=[r for r in history if r['検知日時']=='2026-11-10 09:00:00'];self.assertEqual(len(events),3);self.assertEqual({r['種別'] for r in events},{'変更'})
        for r in csv.reader(io.StringIO(old_diff)):
            self.assertIn(r,list(csv.reader(io.StringIO((self.root/'data/diff/latest.csv').read_text()))))
        self.assertEqual(len(list(csv.DictReader(io.StringIO((self.root/A.EVIDENCE).read_text())))),3)
        self.assertEqual(receipt['count'],3)
        saved=self.snapshot();self.assertEqual(A.apply(self.root,now=NOW)['status'],'ALREADY_APPLIED');self.assertEqual(self.snapshot(),saved)
    def test_changed_row_or_approval_blocks_with_no_writes(self):
        p=self.root/'data/master/master.csv';rows=_read_rows(p.read_text());rows['nedas']['last_updated_ms']='1';M.save(rows,p)
        saved=self.snapshot()
        with self.assertRaises(ValueError):A.apply(self.root,now=NOW)
        self.assertEqual(self.snapshot(),saved)
        rows['nedas']=self.bundle['cases'][2]['before'];M.save(rows,p);(self.root/V.APPROVAL).write_bytes(b'{}');saved=self.snapshot()
        with self.assertRaises(ValueError):A.apply(self.root,now=NOW)
        self.assertEqual(self.snapshot(),saved)
    def test_expiry_duplicate_or_corrupt_evidence_blocks(self):
        with self.assertRaises(ValueError):A.apply(self.root,now=datetime(2026,11,26,tzinfo=timezone.utc))
        p=self.root/self.bundle['notice']['raw_path'];p.write_bytes(b'broken');saved=self.snapshot()
        with self.assertRaises((ValueError,OSError)):A.apply(self.root,now=NOW)
        self.assertEqual(self.snapshot(),saved)
    def test_transaction_failure_rolls_back_every_output(self):
        saved=self.snapshot()
        from src import persistence
        original=persistence.os.replace;count=0
        def fail_once(src,dst):
            nonlocal count
            count+=1
            if count==4:raise OSError('injected replacement failure')
            return original(src,dst)
        with patch.object(persistence.os,'replace',side_effect=fail_once):
            with self.assertRaises(OSError):A.apply(self.root,now=NOW)
        self.assertEqual(self.snapshot(),saved)

class VesselPublisherJournalTests(VesselFixture):
    def git(self,*args):
        return subprocess.run(['git',*args],cwd=self.root,check=True,capture_output=True,text=True).stdout
    def run_script(self):
        env=dict(os.environ,GITHUB_REF='refs/heads/main',PYTHONPATH=os.environ.get('PYTHONPATH',''),PATH=str(self.root/'test-bin')+os.pathsep+os.environ['PATH'])
        return subprocess.run(['bash','.github/scripts/apply-approved-vessel-history.sh'],cwd=self.root,env=env,capture_output=True,text=True)
    def test_real_origin_publish_and_durable_journal_checks(self):
        shutil.copytree(ROOT/'src',self.root/'src',ignore=shutil.ignore_patterns('__pycache__'))
        p=self.root/'.github/scripts';p.mkdir(parents=True);shutil.copyfile(ROOT/'.github/scripts/apply-approved-vessel-history.sh',p/'apply-approved-vessel-history.sh')
        # Freeze only the application clock in this isolated test subprocess.
        # The real shell, CLI parsing, migration, Git commit and push still run.
        shim=self.root/'test-bin/python';shim.parent.mkdir()
        shim.write_text('#!'+sys.executable+'\n'+"""import os,sys
if sys.argv[1:3]==['-m','src.approved_vessel_history']:
    sys.path.insert(0,os.getcwd())
    from datetime import datetime,timezone
    from unittest.mock import patch
    from src import approved_vessel_history as A
    class FixedClock(datetime):
        @classmethod
        def now(cls,tz=None):return datetime(2026,11,10,tzinfo=timezone.utc)
    with patch.object(A,'datetime',FixedClock):raise SystemExit(A.main(sys.argv[3:]))
os.execv("""+repr(sys.executable)+""",["""+repr(sys.executable)+""",*sys.argv[1:]])
""")
        shim.chmod(0o755)
        (self.root/'.gitignore').write_text('__pycache__/\n')
        remote=self.root.parent/(self.root.name+'-origin.git');self.addCleanup(shutil.rmtree,remote,True)
        subprocess.run(['git','init','--bare',str(remote)],check=True,capture_output=True)
        self.git('init','-b','main');self.git('config','user.name','Test');self.git('config','user.email','test@example.invalid');self.git('add','.');self.git('commit','-m','fixture');self.git('remote','add','origin',str(remote));self.git('push','-u','origin','main')
        result=self.run_script();self.assertEqual(result.returncode,0,result.stderr)
        first=self.git('rev-parse','HEAD').strip();self.assertEqual(self.git('rev-parse','origin/main').strip(),first)
        result=self.run_script();self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(self.git('rev-parse','HEAD').strip(),first)
        receipt=self.root/A.RECEIPT;body=receipt.read_bytes();receipt.write_bytes(body+b' ')
        self.assertNotEqual(self.run_script().returncode,0)
        receipt.write_bytes(body);altered=json.loads(body);altered['applied_at']='2026-11-09T01:00:00Z';receipt.write_text(json.dumps(altered))
        self.git('add',A.RECEIPT.as_posix());self.git('commit','-m','unpublished journal')
        self.assertNotEqual(self.run_script().returncode,0)
        self.git('reset','--hard',first);receipt.write_text('{}')
        self.assertNotEqual(self.run_script().returncode,0)
