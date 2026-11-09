"""Publish the three explicitly approved historical vessel strings once.

No current source state, new source candidate, timestamps or other master row is
promoted by this metadata migration. The ordinary monitor verifies current IMO
and Party bindings independently before publishing its own candidate.
"""
from __future__ import annotations
import argparse,copy,csv,hashlib,io,json,re,shutil,tempfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
from . import vessel_history as V,integrity_triage as T,integrity_decisions as Q
from . import integrity_legacy as L,master as M,dashboard as D
from .mof_legacy_review import _read_rows
from .approved_legacy_delist import _json,DIFF_COLS
from .persistence import FileWrite,atomic_replace_many

ROOT=Path(__file__).resolve().parent.parent
LEDGER='data/review/integrity_retention_decisions.json'
EVIDENCE='data/evidence/ofac_vessel_name_history.csv'
RECEIPT=Path('data/monitoring/approved_vessel_history_20261109_applied.json')
TARGETS=('data/master/master.csv',LEDGER,'data/dashboard/list.csv','data/dashboard/screening.csv.gz',
         'data/dashboard/changes.csv','data/diff/latest.csv',EVIDENCE)
EVIDENCE_COLS=['旧船名','公表時点の変更後名','名称区分','FixedRef','IMO','公表日','保持承認記録日時UTC','再レビュー期限UTC','一次資料URL','一次資料SHA256']

def apply(root:Path=ROOT,*,now:datetime|None=None,run_url:str='')->dict:
    root=Path(root).resolve();now=now or datetime.now(timezone.utc)
    if now.tzinfo is None or (run_url and not re.fullmatch(r'https://github\.com/[\w.-]+/[\w.-]+/actions/runs/[0-9]+',run_url)):
        raise ValueError('invalid application timestamp or run URL')
    bundle=V.approval(root);cases={c['master_key']:c for c in bundle['cases']}
    rows=_read_rows((root/'data/master/master.csv').read_text(encoding='utf-8'))
    ledger=json.loads((root/LEDGER).read_bytes())
    Q.apply(dict(findings=[]),dict(items=[]),ledger,now=now) # validate ledger structure and dates
    entries={e['review_id']:e for e in ledger['entries']}
    if (root/RECEIPT).exists():
        receipt=json.loads((root/RECEIPT).read_bytes())
        if (receipt.get('status')!='APPLIED' or receipt.get('count')!=3 or receipt.get('approval_sha256')!=V.APPROVAL_SHA256
                or any(rows.get(k)!=c['after'] or entries.get(c['retention_decision']['review_id'])!=c['retention_decision'] for k,c in cases.items())):
            raise ValueError('previous vessel application incomplete or changed; fresh review required')
        return dict(receipt,status='ALREADY_APPLIED')
    if not Q._date(bundle['reviewed_at'])<=now<Q._date(bundle['expires_at']):raise ValueError('vessel approval not yet valid or expired')
    candidate=copy.deepcopy(rows);new_ledger=copy.deepcopy(ledger)
    for key,c in cases.items():
        if (rows.get(key)!=c['before'] or T.digest(c['after'])!=c['after_sha256']
                or c['retention_decision']['review_id'] in entries or c['after']['status']!='有効'
                or any(c['before'][f]!=c['after'][f] for f in M.FIELDS if f not in {'risk_type','review_flag','remark'})):
            raise ValueError(f'approved vessel row changed or decision already exists: {key}')
        V.static_evidence(root,c,bundle)
        current=V._vessel([dict(source_id=r[0],name=r[1],raw_record=r) for r in bundle['current_verification']['records']],c)
        if current['name']!=c['replacement_name']:raise ValueError('reviewed vessel identity changed')
        candidate[key]=copy.deepcopy(c['after']);new_ledger['entries'].append(copy.deepcopy(c['retention_decision']))
    Q.apply(dict(findings=[]),dict(items=[]),new_ledger,now=now)
    L.validate_for_export(root,list(candidate.values()),now=now)
    stamp=now.astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
    events=[['OFAC','変更',c['old_name'],c['before']['remark'],c['after']['remark']] for c in bundle['cases']]
    with (root/'data/diff/latest.csv').open(encoding='utf-8',newline='') as stream:
        reader=csv.reader(stream,strict=True)
        if next(reader,[])!=DIFF_COLS:raise ValueError('latest diff schema changed')
        previous=list(reader)
    if any(len(r)!=len(DIFF_COLS) for r in previous):raise ValueError('malformed latest diff')
    with tempfile.TemporaryDirectory(prefix='approved-vessel-history-') as folder:
        stage=Path(folder)
        for rel in ('data/dashboard/changes.csv','data/review/ofac_party_removal_queue.csv'):
            if (root/rel).exists():
                (stage/rel).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/rel,stage/rel)
        M.save(candidate,stage/'data/master/master.csv')
        if _read_rows((stage/'data/master/master.csv').read_text(encoding='utf-8'))!=candidate:raise ValueError('unapproved master projection')
        _json(stage/LEDGER,new_ledger);D.write_list(stage,candidate);D.write_screening_gzip(stage,candidate);L._display(stage,list(candidate.values()))
        D.append_changes(stage,events,when=stamp)
        path=stage/'data/diff/latest.csv';path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('w',encoding='utf-8',newline='') as stream:csv.writer(stream).writerows([DIFF_COLS,*events,*[r for r in previous if r not in events]])
        path=stage/EVIDENCE;path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('w',encoding='utf-8',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(EVIDENCE_COLS)
            for c in bundle['cases']:writer.writerow([c['old_name'],c['replacement_name'],'旧船名（履歴）',c['party_id'],c['imo'],c['notice_date'],bundle['reviewed_at'],bundle['expires_at'],bundle['notice']['source_url'],bundle['notice']['source_hash']])
        receipt=dict(schema_version=1,status='APPLIED',count=3,approval_sha256=V.APPROVAL_SHA256,
            approved_date=bundle['approved_date'],reviewed_at=bundle['reviewed_at'],expires_at=bundle['expires_at'],
            applied_at=now.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),run_url=run_url,
            master_before_sha256=hashlib.sha256((root/'data/master/master.csv').read_bytes()).hexdigest(),
            master_after_sha256=hashlib.sha256((stage/'data/master/master.csv').read_bytes()).hexdigest(),
            outputs={rel:hashlib.sha256((stage/rel).read_bytes()).hexdigest() for rel in TARGETS})
        _json(stage/RECEIPT,receipt)
        atomic_replace_many([FileWrite(root/rel,lambda temp,rel=rel:shutil.copyfile(stage/rel,temp)) for rel in (*TARGETS,RECEIPT.as_posix())])
    return receipt

def main(argv=None):
    import os
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,default=ROOT);parser.add_argument('--apply',required=True,action='store_true');args=parser.parse_args(argv)
    repo,run=os.getenv('GITHUB_REPOSITORY',''),os.getenv('GITHUB_RUN_ID','')
    print(json.dumps(apply(args.root,run_url=f'https://github.com/{repo}/actions/runs/{run}' if repo and run else ''),ensure_ascii=False));return 0
if __name__=='__main__':raise SystemExit(main())
