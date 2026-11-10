"""Apply the one explicitly approved November10 MOF review as one generation."""
from __future__ import annotations
import argparse,copy,csv,gzip,hashlib,io,json,os,re,shutil,tempfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
from . import master as M,dashboard as D,state as S,source_audit as SA
from . import mof_pending as P,mof_record_diff as R,mof_re_review as RR
from . import integrity_audit as I,integrity_legacy as L
from .fetch import fetch
from .sources import mof
from .persistence import FileWrite,atomic_replace_many

ROOT=Path(__file__).resolve().parent.parent
APPROVAL=Path('data/review/approved_mof_source_update_20261110.json')
APPROVAL_SHA256='844ef4a7eec010950d3e53e154202c77cfb0e6a2f80a0efdc6749aa98465029e'
PROPOSAL=Path('docs/evidence/mof-source-review-20261110/proposed-changes.json.gz')
PROPOSAL_SHA256='23381e958a4dd1f50259667b3a0c57017c34dd545db23f1d26460d32357daa00'
RECEIPT=Path('data/monitoring/approved_mof_source_update_20261110_applied.json')
AUDIT=Path('data/monitoring/approved_mof_source_update_20261110_audit.json.gz')
EVIDENCE=Path('data/monitoring/approved_mof_source_update_20261110_evidence.json.gz')
PROMOTIONS=Path('data/monitoring/mof_source_promotion_history.csv')
APPROVED_DIFF=Path('data/monitoring/approved_mof_source_update_20261110_diff.csv')
LEDGER=Path('data/review/integrity_retention_decisions.json')
JST=timezone(timedelta(hours=9))
TAG='MOF承認反映20261110'


def _sha(body):return hashlib.sha256(body).hexdigest()
def _json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def _date(s):return datetime.strptime(s,'%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


def current_source(proposal):
    """Fresh official discovery and full bytes; 304 cannot approve an old source."""
    import requests
    with requests.Session() as session:
        url,_,_=mof.discover(session=session)
        if url!=proposal['source_url']:raise ValueError('MOF official URL changed; new review required')
        return fetch(url,session=session)


def _source(proposal):
    f=current_source(proposal)
    if (f.url!=proposal['source_url'] or not f.body or f.not_modified or f.http_status!=200
            or f.sha256!=proposal['source_after_sha256'] or _sha(f.body)!=f.sha256):
        raise ValueError('MOF official bytes differ from approved source')
    return f


def apply(root=ROOT,*,now=None,run_url=''):
    root=Path(root).resolve();now=now or datetime.now(timezone.utc)
    if now.tzinfo is None or (run_url and not re.fullmatch(r'https://github\.com/[\w.-]+/[\w.-]+/actions/runs/[0-9]+',run_url)):
        raise ValueError('invalid application time/run URL')
    abody=(root/APPROVAL).read_bytes();pbody=(root/PROPOSAL).read_bytes()
    if _sha(abody)!=APPROVAL_SHA256 or _sha(pbody)!=PROPOSAL_SHA256:raise ValueError('approved MOF artifact hash changed')
    approval=json.loads(abody);proposal=json.loads(gzip.decompress(pbody))
    if (approval['decision']!='APPROVED_APPLY_MOF_SOURCE_UPDATE' or approval['proposal_sha256']!=PROPOSAL_SHA256
            or approval['row_count']!=358 or approval['pending_count']!=79 or approval['source_sha256']!=proposal['source_after_sha256']):
        raise ValueError('MOF approval scope changed')
    if (root/RECEIPT).exists():
        old=json.loads((root/RECEIPT).read_bytes())
        if (old.get('status')!='APPLIED' or old.get('count')!=358
                or old.get('approval_sha256')!=APPROVAL_SHA256 or old.get('proposal_sha256')!=PROPOSAL_SHA256):
            raise ValueError('MOF application receipt invalid')
        return dict(old,status='ALREADY_APPLIED')
    if not _date(approval['approval_recorded_at'])<=now<_date(approval['expires_at']):raise ValueError('MOF approval future or expired')
    state=S.load_state(root)
    if state.get('mof')!=proposal['source_state_before']:raise ValueError('MOF applied source state changed')
    cursor=R._load_state(root)
    before_raw=root/cursor['processed_raw']
    if (cursor.get('processed_content_sha256')!=proposal['source_before_sha256']
            or R._parse_csv(before_raw).content_hash!=proposal['source_before_sha256']):
        raise ValueError('MOF source-record baseline changed')
    approved=P._observations([[r[c] for c in P.COLUMNS] for r in proposal['pending_candidates']])
    observed=P._observations(P.read_csv(root))
    if set(observed)!=set(approved) or len(observed)!=79:raise ValueError('MOF pending case scope changed')
    for key,row in observed.items():
        if row[:13]+row[14:]!=approved[key][:13]+approved[key][14:]:raise ValueError('MOF pending evidence or first detection changed')
    rows=M.load(root/'data/master/master.csv');cases=proposal['cases']
    if len(cases)!=358 or len({c['match_key'] for c in cases})!=358:raise ValueError('MOF row scope changed')
    for c in cases:
        if rows.get(c['match_key'])!=c['before']:raise ValueError('MOF approved before row changed: '+c['match_key'])
    ledger=json.loads((root/LEDGER).read_bytes());entries={e['review_id']:e for e in ledger['entries']}
    withdrawals=proposal['retention_decisions_change']['withdrawn']
    if len(withdrawals)!=4 or any(entries.get(e['prior_decision']['review_id'])!=e['prior_decision'] for e in withdrawals):
        raise ValueError('MOF retention withdrawal scope changed')
    f=_source(proposal)
    candidate=copy.deepcopy(rows);applied_ms=str(int(now.timestamp()*1000))
    for c in cases:
        candidate[c['match_key']]=copy.deepcopy(c['after'])
        candidate[c['match_key']]['last_updated_ms']=applied_ms
        if c['before'] and candidate[c['match_key']]['first_seen_ms']!=c['before']['first_seen_ms']:
            raise ValueError('MOF original registration time changed')
    removed={e['prior_decision']['review_id'] for e in withdrawals}
    ledger['entries']=[e for e in ledger['entries'] if e['review_id'] not in removed]
    applied_at=now.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    rawrel=Path('data/raw/mof')/(now.strftime('%Y%m%dT%H%M%SZ')+'__shisantouketsu20261105.csv.gz')
    source_stamp=proposal['first_detected_jst'];quality_stamp=_date(proposal['reviewed_at']).astimezone(JST).strftime('%Y-%m-%d %H:%M:%S')
    events=[];detections={}
    for c in cases:
        row=candidate[c['match_key']];kind=c['kind'];before=c['before'] or {}
        label={'OFFICIAL_PARTY_DELIST':'正式掲載終了','NAME_REVISION_CURRENT_PARTY':'名称表記更新（本人の掲載終了ではない）',
               'WEAK_ALIAS_REFERENCE_ONLY':'Weak Alias参考情報化','REACTIVATE_CURRENT_NAME':'現行名称の復帰',
               'ADD_CURRENT_NAME':'現行名称の追加','ADD_CURRENT_MOF_SOURCE':'財務省の根拠を追加'}[kind]
        ids=sorted({p['source_id'] for p in c['official_mof']} or set(c.get('old_source_ids',[])))
        after=f"{row['remark']}\n{TAG}: {label}; 状態={row['status']}; 原本番号={';'.join(ids)}"
        typ='掲載終了' if kind=='OFFICIAL_PARTY_DELIST' else ('追加' if kind=='ADD_CURRENT_NAME' else '変更')
        event=['財務省',typ,row['display_name'],before.get('remark','未登録'),after]
        events.append(event);detections[tuple(event)]=quality_stamp if kind=='WEAK_ALIAS_REFERENCE_ONLY' else source_stamp
    with tempfile.TemporaryDirectory(prefix='approved-mof-source-') as folder:
        stage=Path(folder)
        for rel in ['data/raw','data/review','data/dashboard','data/source_record_state','data/source_diff','data/heartbeat','data/source_audit','data/monitoring','docs/evidence']:
            if (root/rel).exists():shutil.copytree(root/rel,stage/rel)
        for rel in ['data/master/ofac_alias_history.csv','data/kubun_map.json','data/diff/latest.csv']:
            if (root/rel).exists():(stage/rel).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/rel,stage/rel)
        (stage/rawrel).parent.mkdir(parents=True,exist_ok=True);(stage/rawrel).write_bytes(gzip.compress(f.body,mtime=0))
        M.save(candidate,stage/'data/master/master.csv');_json(stage/LEDGER,ledger)
        state['mof']=copy.deepcopy(proposal['source_state_after']);S.save_state(stage,state)
        diff=R.compare_snapshots(R._parse_csv(before_raw),R._parse_csv(stage/rawrel))
        if len(diff.amended)!=76 or len(diff.material_amended)!=54 or len(diff.removed)!=3 or diff.added:
            raise ValueError('MOF approved raw diff scope changed')
        R._write_source_diff(stage,[diff],source_stamp)
        for c in diff.material_amended:
            event=['財務省','変更',c.name,f'{c.kind}: {c.before}',f'{c.kind}: {c.after}\n{TAG}: 原本情報改訂（名簿名称の掲載終了ではない）']
            events.append(event);detections[tuple(event)]=source_stamp
        if len(events)!=412:raise ValueError('MOF event scope changed')
        _json(stage/'data/source_record_state/mof.json',dict(processed_raw=rawrel.as_posix(),processed_content_sha256=f.sha256,
            processed_at=now.astimezone(JST).strftime('%Y-%m-%d %H:%M:%S%z'),record_count=diff.after_count,
            last_diff=dict(source_changed=True,material_changed=True,added=0,removed=3,amended=9,raw_amended=9,field_changes=54,
                           raw_field_changes=76,cosmetic_changes=9,aggregate_changes=9,structure_changes=4,dashboard_rows=54,transitions=1)))
        RR.run(stage)
        P.write_csv(stage,[])
        _json(stage/'data/monitoring/mof_candidate.json',dict(schema_version=1,status='APPLIED',sha256=f.sha256,url=f.url,raw_path=rawrel.as_posix(),
            checked_at=applied_at,pending_count=0,master_applied=True,run_url=run_url,first_detected_at=proposal['first_detected_at']))
        D.write_list(stage,candidate);D.write_screening_gzip(stage,candidate)
        D.append_changes(stage,events,when=source_stamp,detected_at=detections)
        with (root/'data/diff/latest.csv').open(encoding='utf-8',newline='') as stream:
            previous=list(csv.reader(stream,strict=True))
        cols=['出所','種別','受取人名','変更前','変更後']
        if not previous or previous[0]!=cols or any(len(r)!=5 for r in previous):raise ValueError('MOF latest diff schema changed')
        with (stage/'data/diff/latest.csv').open('w',encoding='utf-8',newline='') as stream:
            csv.writer(stream,lineterminator='\n').writerows([cols,*events,*[r for r in previous[1:] if r not in events]])
        with (stage/APPROVED_DIFF).open('w',encoding='utf-8',newline='') as stream:
            csv.writer(stream,lineterminator='\n').writerows([cols,*events])
        history_cols=P.COLUMNS+['反映日時UTC','反映実行URL']
        path=stage/PROMOTIONS;path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists():
            with path.open(encoding='utf-8',newline='') as stream:
                if next(csv.reader(stream),None)!=history_cols:raise ValueError('MOF promotion history schema changed')
        with path.open('a',encoding='utf-8',newline='') as stream:
            w=csv.writer(stream,lineterminator='\n')
            if stream.tell()==0:w.writerow(history_cols)
            w.writerows([list(approved[key])+[applied_at,run_url] for key in sorted(approved)])
        hb=S.heartbeat(stage,[dict(source='mof',status='changed',content_hash=f.sha256,source_updated=state['mof']['asof'],record_count=state['mof']['record_count'],raw_path=rawrel.as_posix())],now=now)
        auditrel=Path('data/source_audit')/f'{now:%Y-%m}.csv'
        SA.append_rows(stage/auditrel,[SA.entry('mof','list_file','approved_applied',fetched=f,source_updated=state['mof']['asof'],record_count=state['mof']['record_count'],raw_path=rawrel.as_posix())],now=now)
        D.write_status(stage,[],state);L.validate_for_export(stage,list(candidate.values()),now=now);L._display(stage,list(candidate.values()))
        audited=I.audit(stage,['ofac','mof'])
        if not audited.get('publication_allowed') or audited.get('blocking_counts'):raise ValueError('MOF application integrity blocked: '+str(audited.get('blocking_counts')))
        (stage/AUDIT).write_bytes(gzip.compress((json.dumps(audited,ensure_ascii=False,sort_keys=True)+'\n').encode(),mtime=0))
        _source(proposal)  # A changed source during the audit vetoes publication.
        evidence=dict(approval=approval,proposal_sha256=PROPOSAL_SHA256,applied_at=applied_at,run_url=run_url,
            source_first_detected_at=proposal['first_detected_at'],quality_first_recorded_at=proposal['reviewed_at'],
            source_sha256=f.sha256,withdrawn_retention_decisions=withdrawals,
            applied_cases=[dict(case=c,applied_after=candidate[c['match_key']]) for c in cases])
        (stage/EVIDENCE).write_bytes(gzip.compress((json.dumps(evidence,ensure_ascii=False)+'\n').encode(),mtime=0))
        targets=['data/master/master.csv',LEDGER.as_posix(),S.STATE,rawrel.as_posix(),'data/source_record_state/mof.json','data/source_diff/mof_latest.csv',
                 'data/dashboard/re_review.csv',P.PATH.as_posix(),'data/monitoring/mof_candidate.json','data/dashboard/list.csv','data/dashboard/screening.csv.gz',
                 'data/dashboard/changes.csv','data/diff/latest.csv',PROMOTIONS.as_posix(),str(hb.relative_to(stage)),auditrel.as_posix(),
                 'data/dashboard/status.csv','data/dashboard/status_detail.csv',AUDIT.as_posix(),EVIDENCE.as_posix(),APPROVED_DIFF.as_posix()]
        result=dict(schema_version=1,status='APPLIED',count=358,approval_sha256=APPROVAL_SHA256,proposal_sha256=PROPOSAL_SHA256,
            approved_at=approval['approval_recorded_at'],applied_at=applied_at,run_url=run_url,source_sha256=f.sha256,
            detected_at=proposal['first_detected_at'],quality_detected_at=proposal['reviewed_at'],events=412,weak_reference_count=313,
            pending_count=0,withdrawn_retention_count=4,audit_blocking_counts=audited.get('blocking_counts',{}),
            master_before_sha256=_sha((root/'data/master/master.csv').read_bytes()),master_after_sha256=_sha((stage/'data/master/master.csv').read_bytes()),
            outputs={rel:_sha((stage/rel).read_bytes()) for rel in targets})
        _json(stage/RECEIPT,result)
        atomic_replace_many([FileWrite(root/rel,lambda tmp,rel=rel:shutil.copyfile(stage/rel,tmp)) for rel in [*targets,RECEIPT.as_posix()]])
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--apply',required=True,action='store_true');p.add_argument('--root',type=Path,default=ROOT);args=p.parse_args()
    repo,run=os.getenv('GITHUB_REPOSITORY',''),os.getenv('GITHUB_RUN_ID','')
    result=apply(args.root,run_url=f'https://github.com/{repo}/actions/runs/{run}' if repo and run else '')
    from .approved_mof_delivery import delivery_needed
    if os.getenv('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as f:
            f.write('applied='+str(result['status']=='APPLIED').lower()+'\n')
            f.write('delivery_needed='+str(delivery_needed(args.root)).lower()+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='outputs'},ensure_ascii=False))
if __name__=='__main__':main()
