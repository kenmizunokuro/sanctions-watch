"""One explicitly approved historical alias; never a general alias-loss waiver."""
from __future__ import annotations
import argparse,copy,csv,hashlib,json,re,shutil,tempfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
from . import integrity_readers as R,integrity_notice_context as N,integrity_triage as T
from . import integrity_decisions as Q,integrity_legacy as L,master as M,dashboard as D
from .vessel_history import _body
from .mof_legacy_review import _read_rows
from .approved_legacy_delist import _json,DIFF_COLS
from .persistence import FileWrite,atomic_replace_many

ROOT=Path(__file__).resolve().parent.parent
APPROVAL=Path('data/review/approved_volta_alias_history_20261110.json')
APPROVAL_SHA256='e73aabbe874096351e4edcba6a374e32c62a823ed1214db1edaf22d11b0b4758'
MARKER='OFAC_ALIAS_HISTORY_V1:'
KEY='voltashippingservicesfzco'
LEDGER='data/review/integrity_retention_decisions.json'
RECEIPT=Path('data/monitoring/approved_volta_alias_history_20261110_applied.json')
EVIDENCE='data/evidence/ofac_volta_alias_history.csv'
TARGETS=('data/master/master.csv',LEDGER,'data/dashboard/list.csv','data/dashboard/screening.csv.gz','data/dashboard/changes.csv','data/diff/latest.csv',EVIDENCE)
URLS={'primary':'https://sanctionslistservice.ofac.treas.gov/api/download/SDN.CSV','alias':'https://sanctionslistservice.ofac.treas.gov/api/download/ALT.CSV','advanced':'https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.XML'}


def approval(root):
    root=Path(root).resolve();path=root/APPROVAL
    if not path.resolve().is_relative_to(root/'data/review'):raise ValueError('alias approval path escapes review directory')
    body=path.read_bytes()
    if hashlib.sha256(body).hexdigest()!=APPROVAL_SHA256:raise ValueError('alias approval hash changed')
    b=json.loads(body)
    if b.get('schema_version')!=1 or b.get('decision')!='APPROVED_RETAIN_HISTORICAL_ALIAS' or b.get('master_key')!=KEY or b.get('party_id')!='58138':
        raise ValueError('alias approval scope changed')
    return b


def _primary(records,b):
    rows=[r for r in records if r['source_id']==b['party_id']]
    if len(rows)!=1 or rows[0]['name']!=b['primary_name'] or rows[0]['raw_record'][2].strip()!='-0-':
        raise ValueError('current entity Party/name/type missing or changed')
    if not re.search(re.escape(b['registration'])+r'(?:\s|;|$)',rows[0]['raw_record'][11]):
        raise ValueError('entity registration changed')
    return rows[0]


def static_evidence(root,b):
    h=b['historical']
    if h['source']!='ofac_sdn' or h['role']!='alias' or h['source_url']!=URLS['alias']:raise ValueError('historical alias source changed')
    rows=R.read_classic(_body(root,h,'docs/evidence/integrity-archives'),'SDN',alias=True)
    if not any(r['source_id']==b['party_id'] and r['name']==b['old_name'] for r in rows):raise ValueError('historical alias/Party original missing')
    notice=b['notice']
    if notice['source_url']!='https://ofac.treasury.gov/recent-actions/20261109':raise ValueError('unapproved notice URL')
    records=N.read_records(_body(root,notice,'docs/evidence/volta-alias-history'))
    rows=[r for r in records if r['record_number']==notice['record_number']]
    if len(rows)!=1:raise ValueError('notice record missing')
    r=rows[0];text=r['record'];parts=text.split(' -to- ')
    if (r['heading']!="The following changes have been made to OFAC's SDN List:" or hashlib.sha256(text.encode()).hexdigest()!=notice['record_sha256'] or len(parts)!=2
            or any(not p.startswith(b['primary_name']+' ') or b['registration'] not in p for p in parts)
            or '(a.k.a. '+b['old_name']+')' not in parts[0] or '(a.k.a. '+b['new_name']+')' not in parts[1]):
        raise ValueError('notice does not prove the approved alias transition')
    return dict(master_key=KEY,party_id=b['party_id'],old_name=b['old_name'],new_name=b['new_name'],notice_url=notice['source_url'],notice_sha256=notice['source_hash'],historical_source_sha256=h['source_hash'],name_status='historical_alias')


def verify_decision(root,report,decision):
    b=approval(root)
    if not isinstance(decision.get('alias_history'),dict) or decision!=b['retention_decision']:raise ValueError('alias decision scope changed')
    facts=static_evidence(root,b);current={}
    for role in URLS:
        docs=[d for d in report.get('documents',[]) if d.get('source')=='ofac_sdn' and d.get('role')==role]
        if len(docs)!=1 or docs[0]['source_url']!=URLS[role]:raise ValueError('current official alias source missing')
        doc=docs[0];folder='data/raw/ofac_sdn_advanced' if role=='advanced' else 'data/raw/ofac_sdn'
        body=_body(root,doc,folder)
        current[role]=R.read_ofac_xml(body,'SDN') if role=='advanced' else R.read_classic(body,'SDN',alias=role=='alias')
        facts['current_'+role+'_sha256']=doc['sha256']
    _primary(current['primary'],b)
    for role in ('alias','advanced'):
        names=[r for r in current[role] if r['source_id']==b['party_id']]
        if any(r['name'].casefold()==b['old_name'].casefold() for r in names):raise ValueError('old alias is current again; re-review required')
        new=[r for r in names if r['name'].casefold()==b['new_name'].casefold()]
        if not new or any(r['weak'] or r['primary'] for r in new):raise ValueError('approved replacement strong alias missing')
        if role=='advanced' and not any(r['name'].casefold()==b['primary_name'].casefold() and r['primary'] and not r['weak'] for r in names):
            raise ValueError('Advanced entity primary name missing')
    return facts


def validate_annotations(root,report,ledger,*,now):
    """A current-again alias must not evade its historical-annotation checks."""
    path=Path(root)/'data/master/master.csv'
    marked=[]
    try:
        if path.exists():
            with path.open(encoding='utf-8',newline='') as stream:
                marked=[r for r in csv.DictReader(stream,strict=True) if MARKER in r.get('review_flag','')]
        decisions=[e for e in ledger['entries'] if 'alias_history' in e]
        if not marked and not decisions:return report
        b=approval(root)
        if marked!=[b['after']] or decisions!=[b['retention_decision']]:raise ValueError('alias annotation or ledger scope changed')
        if not Q._date(b['reviewed_at'])<=now<Q._date(b['expires_at']):raise ValueError('alias annotation approval future or expired')
        verified=any(r.get('review_id')==b['retention_decision']['review_id'] and r.get('status')=='APPLIED' and r.get('alias_history') for r in report.get('retention_review',[]))
        if not verified:verify_decision(root,report,decisions[0])
    except (OSError,ValueError,KeyError,TypeError,EOFError,csv.Error) as exc:
        report['findings'].append(dict(kind='ALIAS_HISTORY_EVIDENCE_INVALID',checked_source='OFAC',detail=str(exc),blocking=True))
    from .integrity_audit import _finish
    return _finish(report)


def apply(root=ROOT,*,now=None,run_url=''):
    root=Path(root).resolve();now=now or datetime.now(timezone.utc)
    if now.tzinfo is None or (run_url and not re.fullmatch(r'https://github\.com/[\w.-]+/[\w.-]+/actions/runs/[0-9]+',run_url)):raise ValueError('invalid application time or run URL')
    b=approval(root);rows=_read_rows((root/'data/master/master.csv').read_text(encoding='utf-8'));ledger=json.loads((root/LEDGER).read_bytes())
    Q.apply(dict(findings=[]),dict(items=[]),ledger,now=now)
    entries={e['review_id']:e for e in ledger['entries']};decision=b['retention_decision']
    if (root/RECEIPT).exists():
        receipt=json.loads((root/RECEIPT).read_bytes())
        if receipt.get('status')!='APPLIED' or receipt.get('count')!=1 or receipt.get('approval_sha256')!=APPROVAL_SHA256 or rows.get(KEY)!=b['after'] or entries.get(decision['review_id'])!=decision:
            raise ValueError('prior alias application changed or incomplete')
        return dict(receipt,status='ALREADY_APPLIED')
    if not Q._date(b['reviewed_at'])<=now<Q._date(b['expires_at']):raise ValueError('alias approval future or expired')
    if (rows.get(KEY)!=b['before'] or T.digest(b['after'])!=b['after_sha256'] or decision['review_id'] in entries
            or b['after']['status']!='有効' or any(b['before'][f]!=b['after'][f] for f in M.FIELDS if f not in {'risk_type','review_flag','remark'})):
        raise ValueError('approved alias row changed')
    static_evidence(root,b)
    _primary([dict(source_id=b['current_verification']['primary_record'][0],name=b['current_verification']['primary_record'][1],raw_record=b['current_verification']['primary_record'])],b)
    candidate=copy.deepcopy(rows);candidate[KEY]=copy.deepcopy(b['after']);new_ledger=copy.deepcopy(ledger);new_ledger['entries'].append(copy.deepcopy(decision))
    Q.apply(dict(findings=[]),dict(items=[]),new_ledger,now=now);L.validate_for_export(root,list(candidate.values()),now=now)
    event=['OFAC','変更',b['old_name'],b['before']['remark'],b['after']['remark']]
    with (root/'data/diff/latest.csv').open(encoding='utf-8',newline='') as stream:previous=list(csv.reader(stream,strict=True))
    if not previous or previous[0]!=DIFF_COLS or any(len(r)!=len(DIFF_COLS) for r in previous):raise ValueError('latest diff schema changed')
    with tempfile.TemporaryDirectory(prefix='approved-alias-history-') as folder:
        stage=Path(folder)
        for rel in ('data/dashboard/changes.csv','data/review/ofac_party_removal_queue.csv'):
            if (root/rel).exists():(stage/rel).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/rel,stage/rel)
        M.save(candidate,stage/'data/master/master.csv');_json(stage/LEDGER,new_ledger)
        if _read_rows((stage/'data/master/master.csv').read_text(encoding='utf-8'))!=candidate:raise ValueError('unapproved alias projection')
        D.write_list(stage,candidate);D.write_screening_gzip(stage,candidate);L._display(stage,list(candidate.values()))
        stamp=now.astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
        D.append_changes(stage,[event],when=stamp)
        path=stage/'data/diff/latest.csv';path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('w',encoding='utf-8',newline='') as f:csv.writer(f).writerows([DIFF_COLS,event,*[r for r in previous[1:] if r!=event]])
        path=stage/EVIDENCE;path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('w',encoding='utf-8',newline='') as f:csv.writer(f).writerows([['旧別名','現行別名','現行主名称','FixedRef','名称区分','公表日','保持承認記録UTC','再レビュー期限UTC','一次資料URL','一次資料SHA256'],[b['old_name'],b['new_name'],b['primary_name'],b['party_id'],'旧別名（履歴）','2026-11-09',b['reviewed_at'],b['expires_at'],b['notice']['source_url'],b['notice']['source_hash']]])
        receipt=dict(schema_version=1,status='APPLIED',count=1,approval_sha256=APPROVAL_SHA256,reviewed_at=b['reviewed_at'],expires_at=b['expires_at'],applied_at=now.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),run_url=run_url,outputs={rel:hashlib.sha256((stage/rel).read_bytes()).hexdigest() for rel in TARGETS})
        _json(stage/RECEIPT,receipt);atomic_replace_many([FileWrite(root/rel,lambda temp,rel=rel:shutil.copyfile(stage/rel,temp)) for rel in (*TARGETS,RECEIPT.as_posix())])
    return receipt


def main(argv=None):
    import os
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=ROOT);p.add_argument('--apply',required=True,action='store_true');args=p.parse_args(argv)
    repo,run=os.getenv('GITHUB_REPOSITORY',''),os.getenv('GITHUB_RUN_ID','')
    print(json.dumps(apply(args.root,run_url=f'https://github.com/{repo}/actions/runs/{run}' if repo and run else ''),ensure_ascii=False));return 0
if __name__=='__main__':raise SystemExit(main())
