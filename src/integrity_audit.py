"""Read-only completeness audit of hash-pinned official bodies and master candidates.

No network, no ingestion parsers, no deletion, no mutation of business data.
CLI nonzero is a publication veto, never a delisting instruction.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import sys
import uuid
import zlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import integrity_readers as R
from .normalize import canonical_display_name, match_key

ROOT = Path(__file__).resolve().parent.parent
REGRESSION_NAMES = ('SEBASTIEN UWIMBABAZI', 'MUHAMMED LUMISA', 'LUMISA, MUHAMAD', 'CHARLES SEMATAMA')


def sha(body):
    return hashlib.sha256(body).hexdigest()


def _surface(name):
    return canonical_display_name(name)


def _finish(result):
    result['counts'] = dict(sorted(Counter(f['kind'] for f in result['findings']).items()))
    blocked=any(f.get('blocking',True) for f in result['findings'])
    result['publication_allowed']=not blocked
    result['status'] = 'BLOCKED' if blocked else ('REVIEW' if result['findings'] else 'PASS')
    return result


def compare(expected: list[dict], master_rows: list[dict], index_rows: list[dict], categories: dict) -> dict:
    """Compare raw record witnesses with persisted rows, preserving duplicate/ID evidence.

    categories maps official list/kubun to the master source:category contract.
    MOF source IDs are reconstructed links; the master schema stores no MOF ID.
    """
    result = dict(findings=[], lineage=[], regression=[])

    def finding(kind, detail, *, official=None, master=None, index=None, blocking=True, checked_source=None):
        item = dict(kind=kind, detail=detail, blocking=blocking)
        if checked_source is not None:item['checked_source']=checked_source
        if official is not None: item['official'] = official
        if master is not None: item['master'] = master
        if index is not None: item['index'] = index
        result['findings'].append(item)

    masters = defaultdict(list)
    for row in master_rows:
        masters[row.get('match_key','')].append(row)
    for key, rows in masters.items():
        if len(rows) > 1:
            finding('DUPLICATE_RECORD',f'masterキー重複: {key}',master=rows)

    expected_keys = defaultdict(list)
    expected_index = defaultdict(list)
    scoped_sources = {r['source'] for r in expected}
    for record in expected:
        key = match_key(record['name'])
        expected_keys[(record['source'],key)].append(record)
        if record['source'] == 'OFAC':
            expected_index[(record['category'],record['source_id'],_surface(record['name']))].append(record)
        rows = masters.get(key,[])
        result['lineage'].append({k:record.get(k,'') for k in (
            'source','category','source_id','un_id','name','primary','weak','source_url','source_hash','raw_path','locator'
        )} | {'master_key':key,'master_rows':len(rows),
              'id_link':'explicit_party_alias_index' if record['source']=='OFAC' else 'reconstructed_by_name_and_category'})
        if record['source']=='財務省' and record['name'] in REGRESSION_NAMES:
            result['regression'].append(dict(name=record['name'],source_id=record['source_id'],
                                             un_id=record.get('un_id',''),primary=record['primary'],
                                             master_key=key,master_rows=rows,source_hash=record.get('source_hash','')))

    for (source,key), records in sorted(expected_keys.items()):
        rows = masters.get(key,[])
        strong = [r for r in records if source!='OFAC' or not r['weak']]
        if not strong:
            for row in rows:
                if set(row.get('sources','').split(';'))=={'OFAC'} and row.get('status')=='有効':
                    finding('WEAK_ALIAS_ACTIVE','Weak-only名称が通常masterで有効',official=records[0],master=row)
            continue
        for record in strong:
            if not rows:
                finding('MISSING_REGISTRATION' if record['primary'] else 'MISSING_ALIAS',
                        '公式掲載名称に対応するmaster行が無い',official=record)
                continue
            category = categories.get(record['category'])
            if record['source']=='財務省' and record['category'] in categories and category is None:
                finding('CATEGORY_LABEL_UNRESOLVED','原本の区分番号は確認済み。設定の表示名が意図的に未確定',
                        official=record,blocking=False)
            elif not category:
                finding('UNDETERMINED','公式カテゴリとmasterカテゴリの対応未定義',official=record)
            for row in rows:
                if row.get('status') != '有効':
                    finding('STATUS_MISMATCH','公式掲載中だがmasterは有効でない（除外理由のレビュー必須）',official=record,master=row)
                if source not in row.get('sources','').split(';') or (category and category not in row.get('categories','').split(';')):
                    finding('SOURCE_MISMATCH','名称は存在するが出典/カテゴリが対応しない',official=record,master=row)

    for row in master_rows:
        claimed_categories=set(row.get('categories','').split(';'))
        category_sources={c.split(':',1)[0] for c in claimed_categories if ':' in c}
        sources = (set(row.get('sources','').split(';')) | category_sources) & scoped_sources
        if not sources:
            continue
        key = row.get('match_key','')
        if match_key(row.get('display_name','')) != key:
            finding('MISGENERATED','表示名と保存キーが不一致',master=row)
        for source in sorted(sources):
            records=expected_keys.get((source,key),[])
            if records:
                supported={categories[r['category']] for r in records if r['category'] in categories}
                claimed={c for c in claimed_categories if c.startswith(source+':')}
                surplus=claimed-supported
                if surplus:
                    finding('SOURCE_MISMATCH','原本に根拠の無いカテゴリ関係: '+ ';'.join(sorted(surplus)),
                            official=records[0],master=row)
            if (source,key) not in expected_keys:
                finding('EXTRA_REGISTRATION','現在原本に根拠の無い出典付き登録（保持行を含む。削除禁止）',master=row,checked_source=source)

    official_parties={(key[0],key[1]) for key in expected_index}
    index_map = defaultdict(list)
    for row in index_rows:
        key = (row.get('list',''),row.get('party_id',''),_surface(row.get('name','')))
        index_map[key].append(row)
        if len(index_map[key]) > 1:
            finding('DUPLICATE_RECORD','Party/Alias履歴キー重複',index=index_map[key])
        if row.get('alias_current') not in {'0','1'} or row.get('party_current') not in {'0','1'}:
            finding('UNDETERMINED','Alias掲載状態値が不明',index=row)
        if row.get('party_current')=='1' and key[:2] not in official_parties:
            finding('SOURCE_ID_MISMATCH','現役Party IDが公式原本に無い',index=row)
        if row.get('alias_current')=='1':
            if row.get('party_current')!='1':
                finding('STATUS_MISMATCH','現役AliasのPartyが非現役',index=row)
            if key not in expected_index:
                finding('SOURCE_ID_MISMATCH','現在の(list, Party ID, name)が公式原本に存在しない',index=row)
        if match_key(row.get('name','')) != row.get('match_key'):
            finding('MISGENERATED','Alias名称と保存キーが不一致',index=row)

    for key, records in sorted(expected_index.items()):
        rows = index_map.get(key,[])
        if not rows:
            finding('MISSING_ALIAS_EVIDENCE','公式Party/Aliasと一致する履歴行が無い',official=records[0])
        for row in rows:
            if row.get('alias_current')!='1' or row.get('party_current')!='1':
                finding('STATUS_MISMATCH','公式掲載中のParty/Aliasが履歴で非現役',official=records[0],index=row)
            weak = all(r['weak'] for r in records)
            primary = any(r['primary'] and r.get('format')!='classic_csv' for r in records)
            if row.get('low_quality')!=str(int(weak)) or row.get('primary')!=str(int(primary)):
                finding('ALIAS_QUALITY_MISMATCH','原本とAlias品質/主名称区分が不一致',official=records[0],index=row)
    return _finish(result)


def _csv(path, required):
    with path.open(encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream,strict=True)
        header=reader.fieldnames or []
        if len(set(header))!=len(header) or not set(required)<=set(header):
            raise R.ReadError(f'監査対象CSV列構造不明: {path}')
        rows=list(reader)
        if any(None in row or any(v is None for v in row.values()) for row in rows):
            raise R.ReadError(f'監査対象CSV行構造不明: {path}')
        return rows


def _body(path):
    try:
        return gzip.decompress(path.read_bytes()) if path.suffix=='.gz' else path.read_bytes()
    except zlib.error as exc:
        raise R.ReadError(f'原本gzip展開不能: {path}: {exc}') from exc


def _pinned(root, key, state, role, documents):
    fields = {'primary':('sha256','raw_prim','url'),
              'alias':('alt_sha256','raw_alt','alt_url'),
              'advanced':('advanced_sha256','raw_advanced','advanced_url'),
              'mof':('sha256','raw_path','url')}
    hfield,pfield,ufield=fields[role]
    digest=state.get(hfield,'')
    if not re.fullmatch('[0-9a-f]{64}',digest):
        raise R.ReadError(f'{key}/{role} state原本hash欠落/不正')
    document=dict(source=key,role=role,sha256=digest,source_url=state.get(ufield,''),raw_path=state.get(pfield,''))
    documents.append(document)
    if not document['source_url'].startswith('https://'):
        raise R.ReadError(f'{key}/{role} 原本URL欠落')
    declared=state.get(pfield,'')
    if declared:
        path=(root/declared).resolve()
        if not path.is_relative_to(root.resolve()/'data/raw'):
            raise R.ReadError(f'{key}/{role} raw_pathが原本領域外')
        body=_body(path)
        if sha(body)!=digest:
            raise R.ReadError(f'{key}/{role} 保存原本hash不一致')
    else:
        # Older MOF states have no raw_path. Select by content hash, never by
        # newest filename or mtime; a failed/newer fetch must not be substituted.
        path=body=None
        for candidate in sorted((root/'data/raw'/key).glob('*')):
            if not candidate.is_file(): continue
            candidate_body=_body(candidate)
            if sha(candidate_body)==digest:
                path,body=candidate,candidate_body;break
        if path is None:
            raise R.ReadError(f'{key}/{role} stateと一致する保存原本無し')
    document['raw_path']=path.relative_to(root).as_posix()
    document['bytes']=len(body)
    return body,document


def audit(root: Path, sources: list[str], upstream_failed: bool = False) -> dict:
    root=root.resolve()
    result=dict(schema_version=1,scope=sorted(set(sources)),documents=[],inputs={},findings=[],lineage=[],regression=[],
                limitations=[
                    'match_key normalization is shared with master; this is not independent proof of matching quality.',
                    'MOF master has no persisted Party ID: ID links are reconstructed from official number/name/category, not independently stored master IDs.',
                    'Only explicit MOF name fields and bounded original-script labels are interpreted; implicit free-text aliases require review.',
                    'Archived hash-pinned snapshot audit, not proof that a remote website is currently unchanged.',
                ])
    expected,categories,index_rows,master_rows=[],{},[],[]
    if upstream_failed:
        result['findings'].append(dict(kind='UPSTREAM_FAILED',detail='取得処理が失敗。旧snapshotの監査結果を今回の正常判定に使わない'))
    try:
        for name in ['data/state.json','data/master/master.csv']+(['data/master/ofac_alias_history.csv'] if 'ofac' in sources else []):
            result['inputs'][name]=sha((root/name).read_bytes())
        state=json.loads((root/'data/state.json').read_text(encoding='utf-8'))
        if not isinstance(state,dict) or any(not isinstance(v,dict) for v in state.values()):
            raise R.ReadError('state JSON構造が不明')
        master_rows=_csv(root/'data/master/master.csv', ['match_key','display_name','status','sources','categories'])
        if not master_rows: raise R.ReadError('masterが空')
        if 'ofac' in sources:
            index_rows=_csv(root/'data/master/ofac_alias_history.csv',['list','party_id','name','match_key','alias_current','party_current','primary','low_quality'])
    except (OSError,ValueError,csv.Error) as exc:
        result['findings'].append(dict(kind='UNDETERMINED',detail=f'監査入力不備: {exc}'))
        return _finish(result)

    keys=(['mof'] if 'mof' in sources else [])+(['ofac_sdn','ofac_cons'] if 'ofac' in sources else [])
    for key in keys:
        try:
            prev=state.get(key,{})
            if key=='mof':
                kpath=root/'data/kubun_map.json'
                result['inputs']['data/kubun_map.json']=sha(kpath.read_bytes())
                kmap=json.loads(kpath.read_text(encoding='utf-8'))['map']
                if not isinstance(kmap,dict) or any(not isinstance(v,str) for v in kmap.values()):
                    raise R.ReadError('財務省カテゴリ対応表構造が不明')
                categories.update({k:('財務省:'+v if v else None) for k,v in kmap.items()})
                body,document=_pinned(root,key,prev,'mof',result['documents'])
                part=R.read_mof(body)
                document['name_count']=len(part)
                for row in part:
                    row.update(source_url=document['source_url'],source_hash=document['sha256'],raw_path=document['raw_path'])
            else:
                label='SDN' if key=='ofac_sdn' else 'Consolidated'
                categories[label]='OFAC:'+label
                parts=[]
                for role in ['primary','alias','advanced']:
                    body,document=_pinned(root,key,prev,role,result['documents'])
                    part=R.read_ofac_xml(body,label) if role=='advanced' else R.read_classic(body,label,alias=role=='alias')
                    document['name_count']=len(part)
                    for row in part:
                        row.update(source_url=document['source_url'],source_hash=document['sha256'],raw_path=document['raw_path'])
                    parts.append(part)
                if {r['source_id'] for r in parts[0]}!={r['source_id'] for r in parts[2]}:
                    raise R.ReadError(f'{key} Classic/Advanced Party ID不一致')
                if not {r['source_id'] for r in parts[1]}<={r['source_id'] for r in parts[0]}:
                    raise R.ReadError(f'{key} Classic Aliasの孤立Party ID')
                part=sum(parts,[])
            expected.extend(part)
        except (OSError,ValueError,KeyError,csv.Error,EOFError) as exc:
            result['findings'].append(dict(kind='UNDETERMINED',source=key,detail=str(exc)))
    # Only compare sources whose complete raw set was decoded. Parse failure is
    # already a veto; never mislabel an unread source as mass missing/delisted.
    # OFAC is an indivisible SDN + Consolidated snapshot.
    if any(f.get('source','').startswith('ofac_') for f in result['findings']):
        expected=[r for r in expected if r['source']!='OFAC']
    complete_labels={r['category'] for r in expected if r['source']=='OFAC'}
    comparison=compare(expected,master_rows,index_rows if complete_labels else [],categories)
    result['findings'].extend(comparison['findings'])
    result['lineage']=comparison['lineage'];result['regression']=comparison['regression']
    result['official_name_count']=len(expected)
    result['master_row_count']=len(master_rows)
    result=_finish(result)
    decision_path=root/'data/review/integrity_retention_decisions.json'
    if decision_path.exists():
        try:
            from . import integrity_triage as T
            from . import integrity_decisions as D
            body=decision_path.read_bytes()
            result['inputs']['data/review/integrity_retention_decisions.json']=sha(body)
            ledger=json.loads(body)
            investigation=T.triage(root,result)
            result=D.apply(result,investigation,ledger)
        except (OSError,ValueError,KeyError,csv.Error) as exc:
            result['findings'].append(dict(kind='RETENTION_DECISION_ERROR',detail=str(exc),blocking=True))
    legacy_path=root/'data/review/integrity_legacy_acceptances.json'
    if legacy_path.exists():
        try:
            from . import integrity_triage as T
            from . import integrity_legacy as L
            body=legacy_path.read_bytes()
            result['inputs']['data/review/integrity_legacy_acceptances.json']=sha(body)
            scope=L.load_scope(root)
            for rel in (L.POLICY_PATH,L.SCOPE_PATH):result['inputs'][rel]=sha((root/rel).read_bytes())
            ledger=json.loads(body)
            result=L.apply(root,result,T.triage(root,result),ledger,scope)
        except (OSError,ValueError,KeyError,TypeError,csv.Error,EOFError,zlib.error) as exc:
            result['findings'].append(dict(kind='LEGACY_ACCEPTANCE_ERROR',detail=str(exc),blocking=True))
    return _finish(result)


def write_report(root: Path, result: dict) -> Path:
    """Append-only runs; exclusive creation prevents overwriting earlier evidence."""
    directory=root/'data/integrity_audit';directory.mkdir(parents=True,exist_ok=True)
    payload=(json.dumps(result,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path=directory/f'{stamp}__{sha(payload)[:16]}__{uuid.uuid4().hex[:8]}.json.gz'
    with path.open('xb') as stream:
        stream.write(gzip.compress(payload,mtime=0))
    return path


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--sources',nargs='+',choices=['ofac','mof','all'],default=['all'])
    parser.add_argument('--upstream-failed',action='store_true')
    parser.add_argument('--triage',action='store_true',help='全findingを根拠付き分類して監査Artifactに保存')
    args=parser.parse_args(argv)
    sources=['ofac','mof'] if 'all' in args.sources else args.sources
    result=audit(args.root,sources,args.upstream_failed)
    triage_paths=[]
    if args.triage:
        try:
            from . import integrity_triage as T
            investigation=T.triage(args.root,result)
            paths=T.write_outputs(args.root/'data/integrity_audit',investigation,stem='triage-'+uuid.uuid4().hex)
            result['triage_summary']=dict(counts=investigation['counts'],retention_candidates=investigation['retention_candidates'],
                                         report_sha256=T.digest(investigation),approved_count=sum(f.get('retention_approved',False) for f in result['findings']))
            triage_paths=[str(p) for p in paths]
        except (OSError,ValueError,KeyError,csv.Error) as exc:
            result['findings'].append(dict(kind='TRIAGE_FAILED',detail=str(exc),blocking=True))
            _finish(result)
    try:
        path=write_report(args.root,result)
    except OSError as exc:
        print(f'AUDIT BLOCKED: 証跡保存失敗: {exc}',file=sys.stderr)
        return 1
    print(json.dumps(dict(status=result['status'],counts=result['counts'],report=str(path),triage_paths=triage_paths),ensure_ascii=False))
    return 0 if result['publication_allowed'] else 1


if __name__=='__main__':
    sys.exit(main())
