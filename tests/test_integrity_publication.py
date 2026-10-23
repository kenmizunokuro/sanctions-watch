"""Execute the actual gate: no publication sentinel after any audit veto."""
import gzip
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from tests import test_integrity_audit as fixtures

REPO=Path(__file__).resolve().parent.parent


class GateTests(unittest.TestCase):
    setUp=fixtures.AuditIOTests.setUp

    def run_gate(self,outcome,sources="mof"):
        env=dict(os.environ,PYTHONPATH=str(REPO),PATH=str(Path(sys.executable).parent)+os.pathsep+os.environ['PATH'])
        return subprocess.run(['bash','-c','set -e; bash "$1" "$3" "$2"; touch published','gate',
                               str(REPO/'.github/scripts/integrity-gate.sh'),outcome,sources],
                              cwd=self.root,env=env,capture_output=True,text=True)

    def test_intact_candidate_reaches_publication(self):
        p=self.run_gate('success');self.assertEqual(p.returncode,0,p.stderr)
        self.assertTrue((self.root/'published').exists())

    def test_bad_candidate_stops_publication_and_preserves_master(self):
        self.mpath.write_text(self.mpath.read_text().replace('有効','無効'))
        before=self.mpath.read_bytes()
        p=self.run_gate('success');self.assertNotEqual(p.returncode,0)
        self.assertFalse((self.root/'published').exists());self.assertEqual(self.mpath.read_bytes(),before)
        reports=list(self.root.glob('data/integrity_audit/*.json.gz'))
        self.assertEqual(len(reports),1)

    def test_declared_mof_cache_path_passes_relative_root(self):
        path=self.root/'data/state.json';state=json.loads(path.read_text())
        state['mof']['raw_path']='data/raw/mof/test.csv.gz';path.write_text(json.dumps(state))
        p=self.run_gate('success');self.assertEqual(p.returncode,0,p.stdout+p.stderr)

    def install_ofac(self,orphan=False):
        import csv,hashlib
        from tests import test_integrity_readers as fixtures_r
        from tests import test_integrity_audit as fixtures_a
        state={}
        for key in ['ofac_sdn','ofac_cons']:
            folder=self.root/'data/raw'/key;folder.mkdir(parents=True)
            item={}
            for role,body in [('prim',b'1,"Jane Doe",individual,SDGT,-0-,-0-,-0-,-0-,-0-,-0-,-0-,-0-\n'),
                              ('alt',b'1,99,aka,"Jane Other",-0-\n'),('advanced',fixtures_r.xml())]:
                path=folder/(role+'.gz');path.write_bytes(gzip.compress(body))
                prefix={'prim':'','alt':'alt_','advanced':'advanced_'}[role]
                item[prefix+'sha256']=hashlib.sha256(body).hexdigest()
                item[prefix+'url']='https://official.test/'+key+'/'+role
                item['raw_'+role]=path.relative_to(self.root).as_posix()
            state[key]=item
        (self.root/'data/state.json').write_text(json.dumps(state))
        rows=[fixtures_a.master(),fixtures_a.master('Jane Other','janeother'),fixtures_a.master('ジェーン','ジェ-ン')]
        for row in rows: row['categories']='OFAC:SDN;OFAC:Consolidated'
        with self.mpath.open('w') as stream:
            writer=csv.DictWriter(stream,fieldnames=rows[0]);writer.writeheader();writer.writerows(rows)
        rows=[]
        for label in ['SDN','Consolidated']:
            for name,key,primary in [('Jane Doe','janedoe','1'),('Jane Other','janeother','0'),('ジェーン','ジェ-ン','1')]:
                rows.append(dict(fixtures_a.index(name,primary=primary,match_key=key),list=label))
        if orphan: rows.append(dict(rows[0],list='SDNN'))
        with (self.root/'data/master/ofac_alias_history.csv').open('w') as stream:
            writer=csv.DictWriter(stream,fieldnames=rows[0]);writer.writeheader();writer.writerows(rows)

    def test_complete_ofac_gate_accepts_realistic_hash_pinned_fixture(self):
        self.install_ofac()
        p=self.run_gate('success','ofac');self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertTrue((self.root/'published').exists())

    def test_unknown_current_index_list_cannot_be_filtered_out(self):
        self.install_ofac(orphan=True)
        # Call with absolute root too, so the unrelated relative-root veto
        # cannot make this test pass without exercising unknown-list handling.
        p=subprocess.run([sys.executable,'-m','src.integrity_audit','--root',str(self.root),'--sources','ofac'],capture_output=True,text=True)
        self.assertNotEqual(p.returncode,0,p.stdout+p.stderr)
        r=json.loads(gzip.decompress(next(self.root.glob('data/integrity_audit/*.json.gz')).read_bytes()))
        self.assertIn('SOURCE_ID_MISMATCH',r['counts'])

    def test_fetch_failure_blocks_even_if_cached_candidate_is_good(self):
        p=self.run_gate('failure');self.assertNotEqual(p.returncode,0)
        self.assertFalse((self.root/'published').exists())
        r=json.loads(gzip.decompress(next(self.root.glob('data/integrity_audit/*.json.gz')).read_bytes()))
        self.assertIn('UPSTREAM_FAILED',r['counts'])


if __name__=='__main__': unittest.main()
