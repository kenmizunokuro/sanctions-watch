"""Resume release/monitor delivery of the committed approved MOF generation."""
from __future__ import annotations
import argparse,gzip,hashlib,json,os,shutil,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
from .approved_mof_source_update import APPROVAL_SHA256,PROPOSAL_SHA256,RECEIPT,AUDIT,APPROVED_DIFF,ROOT

DELIVERY=Path('data/monitoring/approved_mof_source_update_20261110_delivered.json')
def _sha(body):return hashlib.sha256(body).hexdigest()


def _receipt(root):
    r=json.loads((root/RECEIPT).read_bytes())
    if (r.get('status')!='APPLIED' or r.get('count')!=358
            or r.get('approval_sha256')!=APPROVAL_SHA256 or r.get('proposal_sha256')!=PROPOSAL_SHA256):
        raise ValueError('MOF committed application receipt is invalid')
    return r


def delivery_needed(root=ROOT):
    root=Path(root).resolve();_receipt(root)
    if not (root/DELIVERY).exists():return True
    r=json.loads((root/DELIVERY).read_bytes())
    if (r.get('status')!='DELIVERED' or r.get('approval_sha256')!=APPROVAL_SHA256
            or r.get('application_receipt_sha256')!=_sha((root/RECEIPT).read_bytes())):
        raise ValueError('MOF delivery completion journal invalid')
    return False


def deliver(root=ROOT,*,run=subprocess.run,run_url=''):
    root=Path(root).resolve();receipt=_receipt(root)
    def git_output(*args):return subprocess.check_output(['git','-C',str(root),*args])
    def command(args):return run(args,cwd=root,check=True)
    command(['git','fetch','origin','+refs/heads/main:refs/remotes/origin/main'])
    for rel in [RECEIPT]+([DELIVERY] if (root/DELIVERY).exists() else []):
        body=(root/rel).read_bytes()
        if git_output('show','HEAD:'+str(rel))!=body or git_output('show','origin/main:'+str(rel))!=body:
            raise ValueError('MOF delivery requires a durably committed journal')
    if not delivery_needed(root):return dict(status='ALREADY_DELIVERED')
    if _sha((root/AUDIT).read_bytes())!=receipt['outputs'][AUDIT.as_posix()]:raise ValueError('MOF audit bytes changed')
    audited=json.loads(gzip.decompress((root/AUDIT).read_bytes()))
    inputs=audited.get('inputs',{})
    if not {'data/master/master.csv','data/state.json','data/review/integrity_retention_decisions.json'}<=set(inputs):
        raise ValueError('MOF delivery generation binding missing')
    def verify_generation():
        for rel,digest in inputs.items():
            if _sha((root/rel).read_bytes())!=digest:raise ValueError('MOF applied material generation superseded or changed: '+rel)
    verify_generation()
    if _sha((root/APPROVED_DIFF).read_bytes())!=receipt['outputs'][APPROVED_DIFF.as_posix()]:raise ValueError('MOF approved diff bytes changed')
    full=root/'dist/black_receiver_name_all.xlsx';out=root/'dist/internal_import_mof_20261110_latest.xlsx'
    command([sys.executable,'-m','src.export_excel'])
    command([sys.executable,'-m','src.export_diff_excel','--diff',str(root/APPROVED_DIFF),'--out',str(out)])
    shutil.copyfile(out,root/'dist/internal_import_latest.xlsx')
    asset_diff=root/'dist/approved_mof/latest.csv';asset_diff.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/APPROVED_DIFF,asset_diff)
    command(['gh','release','upload','latest',str(full),str(asset_diff),str(root/'dist/internal_import_latest.xlsx'),'--clobber'])
    latest=root/'data/diff/latest.csv';before_latest=latest.read_bytes()
    command([sys.executable,'-m','src.watch','--sources','mof'])
    verify_generation()
    latest.write_bytes(before_latest)
    result=dict(schema_version=1,status='DELIVERED',approval_sha256=APPROVAL_SHA256,
                application_receipt_sha256=_sha((root/RECEIPT).read_bytes()),verified_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                run_url=run_url,material_inputs=inputs,
                assets={p.name:_sha(p.read_bytes()) for p in [full,out,asset_diff]})
    (root/DELIVERY).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    staged=[str(DELIVERY)]+[r for r in ['data/heartbeat','data/source_audit','data/dashboard/status.csv','data/dashboard/status_detail.csv'] if (root/r).exists()]
    command(['git','add','--',*staged])
    command(['git','-c','user.name=sanctions-watch[bot]','-c','user.email=sanctions-watch@users.noreply.github.com',
             'commit','-m','chore(mof): complete approved release delivery and live monitor verification'])
    command(['git','push','origin','HEAD:refs/heads/main'])
    command(['git','fetch','origin','+refs/heads/main:refs/remotes/origin/main'])
    if git_output('show','origin/main:'+str(DELIVERY))!=(root/DELIVERY).read_bytes():raise ValueError('MOF delivery journal not published')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=ROOT);args=p.parse_args()
    repo,run_id=os.getenv('GITHUB_REPOSITORY',''),os.getenv('GITHUB_RUN_ID','')
    print(json.dumps(deliver(args.root,run_url=f'https://github.com/{repo}/actions/runs/{run_id}' if repo and run_id else ''),ensure_ascii=False))
if __name__=='__main__':main()
