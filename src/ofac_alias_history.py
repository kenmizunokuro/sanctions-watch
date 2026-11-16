"""Exact, source-backed approvals for historical OFAC aliases."""
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
HUISTAS_KEY = 'loshuistasdto'
HUISTAS_APPROVAL = Path('data/review/approved_huistas_alias_history_20261116.json')
HUISTAS_APPROVAL_SHA256 = '60aec06c349f0722ba1632c351c5ab78d50be83f5da249eb1c91488fa3715df1'
HUISTAS_RECEIPT = Path('data/monitoring/approved_huistas_alias_history_20261116_applied.json')
HUISTAS_EVIDENCE = 'data/evidence/ofac_huistas_alias_history.csv'
URLS={'primary':'https://sanctionslistservice.ofac.treas.gov/api/download/SDN.CSV','alias':'https://sanctionslistservice.ofac.treas.gov/api/download/ALT.CSV','advanced':'https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.XML'}


def approval(root, *, huistas=False):
    root=Path(root).resolve();path=root/(HUISTAS_APPROVAL if huistas else APPROVAL)
    if not path.resolve().is_relative_to(root/'data/review'):raise ValueError('alias approval path escapes review directory')
    body=path.read_bytes()
    expected_hash = HUISTAS_APPROVAL_SHA256 if huistas else APPROVAL_SHA256
    if hashlib.sha256(body).hexdigest()!=expected_hash:raise ValueError('alias approval hash changed')
    b=json.loads(body)
    expected_scope = (HUISTAS_KEY, '32452') if huistas else (KEY, '58138')
    if (b.get('schema_version')!=1 or b.get('decision')!='APPROVED_RETAIN_HISTORICAL_ALIAS'
            or (b.get('master_key'), b.get('party_id')) != expected_scope):
        raise ValueError('alias approval scope changed')
    return b


def _primary(records,b):
    rows=[r for r in records if r['source_id']==b['party_id']]
    if len(rows)!=1 or rows[0]['name']!=b['primary_name'] or rows[0]['raw_record'][2].strip()!='-0-':
        raise ValueError('current entity Party/name/type missing or changed')
    if not re.search(re.escape(b['registration'])+r'(?:\s|;|$)',rows[0]['raw_record'][11]):
        raise ValueError('entity registration changed')
    return rows[0]


def _huistas_evidence(root, bundle):
    historical = bundle['historical']
    if historical['source_url'] != URLS['advanced']:
        raise ValueError('historical Huistas source URL changed')
    body = _body(root, historical, 'docs/evidence/integrity-archives/ofac_sdn_advanced')
    matches = [row for row in R.read_ofac_xml(body, 'SDN')
               if row['name'].casefold() == bundle['old_name'].casefold()]
    if (len(matches) != 1 or matches[0]['source_id'] != bundle['party_id']
            or matches[0]['locator'] != historical['locator']
            or not matches[0]['weak'] or matches[0]['primary']):
        raise ValueError('historical Huistas Weak Alias/Party original missing or ambiguous')
    return dict(master_key=HUISTAS_KEY, party_id=bundle['party_id'], old_name=bundle['old_name'],
                primary_name=bundle['primary_name'], notice_url=bundle['notice']['source_url'],
                historical_source_sha256=historical['source_hash'], name_status='historical_weak_alias')


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
    scope = decision.get('alias_history')
    if not isinstance(scope, dict):
        raise ValueError('alias decision scope changed')
    huistas = scope.get('master_key') == HUISTAS_KEY
    b=approval(root, huistas=huistas)
    if decision!=b['retention_decision']:raise ValueError('alias decision scope changed')
    facts=_huistas_evidence(root,b) if huistas else static_evidence(root,b)
    current={}
    for role in URLS:
        docs=[d for d in report.get('documents',[]) if d.get('source')=='ofac_sdn' and d.get('role')==role]
        if len(docs)!=1 or docs[0]['source_url']!=URLS[role]:raise ValueError('current official alias source missing')
        doc=docs[0];folder='data/raw/ofac_sdn_advanced' if role=='advanced' else 'data/raw/ofac_sdn'
        body=_body(root,doc,folder)
        current[role]=R.read_ofac_xml(body,'SDN') if role=='advanced' else R.read_classic(body,'SDN',alias=role=='alias')
        facts['current_'+role+'_sha256']=doc['sha256']
    if huistas:
        primary = [r for r in current['primary'] if r['source_id'] == b['party_id']]
        if (len(primary) != 1 or primary[0]['name'] != b['primary_name']
                or primary[0]['raw_record'][2].strip() != '-0-'):
            raise ValueError('current Huistas entity primary name missing or changed')
        old = b['old_name'].casefold()
        if (old in primary[0]['raw_record'][11].casefold()
                or any(r['name'].casefold() == old for rows in current.values() for r in rows)):
            raise ValueError('old Huistas alias is current again; re-review required')
        if not any(r['source_id'] == b['party_id'] and r['primary'] and not r['weak']
                   and r['name'].casefold() == b['primary_name'].casefold() for r in current['advanced']):
            raise ValueError('Advanced Huistas primary name missing')
        return facts
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
        expected_count = 0
        for huistas, key in ((False, KEY), (True, HUISTAS_KEY)):
            indexed_rows = [r for r in marked if r['match_key'] == key]
            case_decisions = [e for e in decisions if isinstance(e['alias_history'], dict)
                              and e['alias_history'].get('master_key') == key]
            if not indexed_rows and not case_decisions:
                continue
            b = approval(root, huistas=huistas)
            decision = b['retention_decision']
            if indexed_rows != [b['after']] or case_decisions != [decision]:
                raise ValueError('alias annotation or ledger scope changed')
            if not Q._date(b['reviewed_at']) <= now < Q._date(b['expires_at']):
                raise ValueError('alias annotation approval future or expired')
            verified = any(r.get('review_id') == decision['review_id']
                           and r.get('status') == 'APPLIED' and r.get('alias_history')
                           for r in report.get('retention_review', []))
            if not verified:
                verify_decision(root, report, decision)
            expected_count += 1
        if len(marked) != expected_count or len(decisions) != expected_count:
            raise ValueError('alias annotation or ledger scope changed')
    except (OSError,ValueError,KeyError,TypeError,EOFError,csv.Error) as exc:
        report['findings'].append(dict(kind='ALIAS_HISTORY_EVIDENCE_INVALID',checked_source='OFAC',detail=str(exc),blocking=True))
    from .integrity_audit import _finish
    return _finish(report)


def apply(root=ROOT,*,now=None,run_url='',huistas=False,fresh_diff=True):
    root=Path(root).resolve();now=now or datetime.now(timezone.utc)
    if now.tzinfo is None or (run_url and not re.fullmatch(r'https://github\.com/[\w.-]+/[\w.-]+/actions/runs/[0-9]+',run_url)):raise ValueError('invalid application time or run URL')
    b=approval(root, huistas=huistas);rows=_read_rows((root/'data/master/master.csv').read_text(encoding='utf-8'));ledger=json.loads((root/LEDGER).read_bytes())
    key = b['master_key']
    receipt_path = HUISTAS_RECEIPT if huistas else RECEIPT
    approval_hash = HUISTAS_APPROVAL_SHA256 if huistas else APPROVAL_SHA256
    evidence_path = HUISTAS_EVIDENCE if huistas else EVIDENCE
    targets = (*TARGETS[:-1], evidence_path)
    Q.apply(dict(findings=[]),dict(items=[]),ledger,now=now)
    entries={e['review_id']:e for e in ledger['entries']};decision=b['retention_decision']
    if (root/receipt_path).exists():
        receipt=json.loads((root/receipt_path).read_bytes())
        if receipt.get('status')!='APPLIED' or receipt.get('count')!=1 or receipt.get('approval_sha256')!=approval_hash or rows.get(key)!=b['after'] or entries.get(decision['review_id'])!=decision:
            raise ValueError('prior alias application changed or incomplete')
        return dict(receipt,status='ALREADY_APPLIED')
    if not Q._date(b['reviewed_at'])<=now<Q._date(b['expires_at']):raise ValueError('alias approval future or expired')
    mutable_fields = {'risk_type', 'review_flag', 'remark'}
    if huistas:
        mutable_fields.update({'status', 'invalid_reason'})
    if (rows.get(key)!=b['before'] or T.digest(b['after'])!=b['after_sha256'] or decision['review_id'] in entries
            or b['after']['status'] != (M.STATUS_INACTIVE if huistas else M.STATUS_ACTIVE)
            or any(b['before'][f]!=b['after'][f] for f in M.FIELDS if f not in mutable_fields)):
        raise ValueError('approved alias row changed')
    if huistas:
        from .integrity_audit import _pinned
        state = json.loads((root/'data/state.json').read_bytes())['ofac_sdn']
        documents = []
        for role in URLS:
            _pinned(root, 'ofac_sdn', state, role, documents)
        verify_decision(root, {'documents': documents}, decision)
    else:
        static_evidence(root,b)
        _primary([dict(source_id=b['current_verification']['primary_record'][0],name=b['current_verification']['primary_record'][1],raw_record=b['current_verification']['primary_record'])],b)
    candidate=copy.deepcopy(rows);candidate[key]=copy.deepcopy(b['after']);new_ledger=copy.deepcopy(ledger);new_ledger['entries'].append(copy.deepcopy(decision))
    Q.apply(dict(findings=[]),dict(items=[]),new_ledger,now=now);L.validate_for_export(root,list(candidate.values()),now=now)
    event=['OFAC','変更',b['old_name'],b['before']['remark'],b['after']['remark']]
    previous = [DIFF_COLS]
    if fresh_diff:
        with (root/'data/diff/latest.csv').open(encoding='utf-8', newline='') as stream:
            previous = list(csv.reader(stream, strict=True))
        if not previous or previous[0]!=DIFF_COLS or any(len(r)!=len(DIFF_COLS) for r in previous):
            raise ValueError('latest diff schema changed')
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
        path=stage/evidence_path;path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            if huistas:
                writer.writerow(['旧別名','現行主名称','FixedRef','名称区分','公表日','保持承認記録UTC','再レビュー期限UTC','公表URL','旧Advanced XML SHA256','旧Advanced XML内の位置'])
                writer.writerow([b['old_name'], b['primary_name'], b['party_id'], '旧Weak Alias（手動照合用）',
                                 b['notice']['publication_date'], b['reviewed_at'], b['expires_at'],
                                 b['notice']['source_url'], b['historical']['source_hash'], b['historical']['locator']])
            else:
                writer.writerows([['旧別名','現行別名','現行主名称','FixedRef','名称区分','公表日','保持承認記録UTC','再レビュー期限UTC','一次資料URL','一次資料SHA256'],[b['old_name'],b['new_name'],b['primary_name'],b['party_id'],'旧別名（履歴）','2026-11-09',b['reviewed_at'],b['expires_at'],b['notice']['source_url'],b['notice']['source_hash']]])
        receipt=dict(schema_version=1,status='APPLIED',count=1,approval_sha256=approval_hash,reviewed_at=b['reviewed_at'],expires_at=b['expires_at'],applied_at=now.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),run_url=run_url,outputs={rel:hashlib.sha256((stage/rel).read_bytes()).hexdigest() for rel in targets})
        _json(stage/receipt_path,receipt);atomic_replace_many([FileWrite(root/rel,lambda temp,rel=rel:shutil.copyfile(stage/rel,temp)) for rel in (*targets,receipt_path.as_posix())])
    return receipt


def main(argv=None):
    import os
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=ROOT);p.add_argument('--apply',required=True,action='store_true');p.add_argument('--huistas', action='store_true');args=p.parse_args(argv)
    repo,run=os.getenv('GITHUB_REPOSITORY',''),os.getenv('GITHUB_RUN_ID','')
    result = apply(args.root, huistas=args.huistas,
                   fresh_diff=not args.huistas or os.getenv('OFAC_FRESH_DIFF') != 'false',
                   run_url=f'https://github.com/{repo}/actions/runs/{run}' if repo and run else '')
    if args.huistas and os.getenv('GITHUB_OUTPUT'):
        with Path(os.environ['GITHUB_OUTPUT']).open('a', encoding='utf-8') as output:
            output.write(f"applied={str(result['status'] == 'APPLIED').lower()}\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0
if __name__=='__main__':raise SystemExit(main())
