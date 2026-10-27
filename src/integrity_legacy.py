"""Exact, expiring acceptance of unresolved legacy screening strings.

This is an explicit policy exception, not verification, an official designation,
identity certification, removal, or an ordinary retention approval. Preparation
only writes a separate candidate and an inactive draft. No network operations.
"""
from __future__ import annotations

import argparse
import copy
import csv
import gzip
import hashlib
import io
import json
import zlib
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import integrity_triage as T
from .integrity_decisions import _date
from .normalize import canonical_display_name, is_trailing_unknown_artifact
from .screening import secondary_screening_key

POLICY_ID = 'phase1-legacy-20261026'
POLICY_PATH = 'docs/evidence/source-integrity-legacy-retention-policy-proposal-20261026.json'
POLICY_SHA = '7d54d514a3c9dfa6708dce5bbec0c6dc595ad6defa5dad4ab0cf26c48c0c4881'
SCOPE_PATH = 'docs/evidence/source-integrity-legacy-scope-20261026.json.gz'
SCOPE_SHA = '8d469074dd140717845339fb573faf6e0f0ffa7563210224a54147bd8d00ccad'
LEDGER_PATH = 'data/review/integrity_legacy_acceptances.json'
DECISION = 'ACCEPT_UNVERIFIED_LEGACY'
MARKER = 'LEGACY_UNVERIFIED_V1:'
LABELS = {
    'ORIGIN_UNRESOLVED': '旧登録・一次出典未確認',
    'NOTICE_ALIAS_FIELD_REVIEW': '旧登録・告知別名の対象関係未確認',
    'NOTICE_MENTION_ONLY': '旧登録・告知言及のみ対象関係未確認',
    'NON_NAME_FIELD_ONLY': '旧登録・名称欄での掲載未確認',
    'WEAK_EVIDENCE_CONFLICT': '旧登録・別名品質の矛盾未解決',
}


def _hash(body):
    return hashlib.sha256(body).hexdigest()


def load_scope(root):
    """Hash pin both scope definition and complete original record/evidence pack."""
    root=Path(root).resolve()
    directory=(root/'docs/evidence').resolve()
    if not directory.is_relative_to(root):raise ValueError('legacy scope directory escapes repository')
    bodies=[]
    for rel in (POLICY_PATH,SCOPE_PATH):
        path=root/rel
        if not path.resolve().is_relative_to(directory):raise ValueError('legacy scope path escapes evidence')
        bodies.append(path.read_bytes())
    policy_body,packed=bodies
    body=gzip.decompress(packed)
    if _hash(policy_body)!=POLICY_SHA or _hash(body)!=SCOPE_SHA:
        raise ValueError('legacy scope hash mismatch')
    policy=json.loads(policy_body);pack=json.loads(body)
    if (pack.get('schema_version')!=1 or pack.get('policy_id')!=POLICY_ID
            or pack.get('proposal_sha256')!=POLICY_SHA
            or pack.get('triage_body_sha256')!=policy['scope']['fixed_triage_body_sha256']):
        raise ValueError('legacy scope provenance mismatch')
    scope={}
    for item in pack['items']:
        ident=item['review_id'];finding=item['finding']
        if (ident in scope or item['classification'] not in LABELS
                or finding['kind'] not in {'EXTRA_REGISTRATION','WEAK_ALIAS_ACTIVE'}
                or item['checked_source']!='OFAC' or finding['master'].get('status')!='有効'
                or T.review_id(finding,item['classification'],item['index_links'],item['evidence'])!=ident
                or T.digest(finding['master'])!=item['master_sha256']
                or T.digest(item['evidence'])!=item['evidence_sha256']):
            raise ValueError('legacy scope record invalid')
        scope[ident]=item
    if (len(scope)!=2429 or set(scope)!=set(policy['scope']['review_ids'])
            or dict(Counter(i['classification'] for i in scope.values()))!=policy['scope']['classification_counts']):
        raise ValueError('legacy fixed scope mismatch')
    return scope


def _term(reviewed_at,expires_at):
    reviewed=_date(reviewed_at);expires=_date(expires_at)
    if not timedelta(0)<expires-reviewed<=timedelta(days=30):
        raise ValueError('legacy term must be positive and at most 30 days')
    return reviewed,expires


def annotate(row,classification,expires_at):
    """Preserve business fields and prior flags; append visible uncertainty."""
    _date(expires_at)
    if classification not in LABELS:raise ValueError('unknown legacy classification')
    if MARKER in row.get('review_flag',''):raise ValueError('legacy row already annotated; renewal requires fresh review')
    result=copy.deepcopy(row)
    result['risk_type']=LABELS[classification]+'／'+row.get('risk_type','')
    result['review_flag']=';'.join(filter(None,[row.get('review_flag',''),MARKER+classification]))
    warning=(f'{LABELS[classification]}。旧登録の照合文字列として期限付き保持。'
             f'現行公式掲載・本人同一性・当初取込意図を確認済みと扱わない。保持期限UTC: {expires_at}')
    result['remark']='\n'.join(filter(None,[row.get('remark',''),warning]))
    return result


def _entry(ident,item,expires_at):
    finding=copy.deepcopy(item['finding'])
    finding['master']=annotate(finding['master'],item['classification'],expires_at)
    return dict(original_review_id=ident,accepted_review_id=T.review_id(finding,item['classification'],item['index_links'],item['evidence']),
                accepted_master_sha256=T.digest(finding['master']),master_key=item['master_key'],
                classification=item['classification'],reviewed_evidence_sha256=item['evidence_sha256'])


def prepare(rows,scope,reviewed_at,expires_at,review_ids=None):
    """Create annotated candidate rows and a DRAFT ledger, never approvals."""
    _term(reviewed_at,expires_at)
    ids=list(scope) if review_ids is None else list(review_ids)
    if not ids or len(set(ids))!=len(ids) or not set(ids)<=scope.keys():raise ValueError('invalid preparation scope')
    positions={}
    for n,row in enumerate(rows):positions.setdefault(row['match_key'],[]).append(n)
    result=copy.deepcopy(rows);entries=[];seen=set()
    for ident in ids:
        item=scope[ident];key=item['master_key'];matches=positions.get(key,[])
        if len(matches)!=1 or key in seen or rows[matches[0]]!=item['finding']['master']:
            raise ValueError(f'legacy original missing, changed, or duplicated: {key}')
        seen.add(key);result[matches[0]]=annotate(rows[matches[0]],item['classification'],expires_at)
        entries.append(_entry(ident,item,expires_at))
    return result,dict(schema_version=1,state='DRAFT',decision=DECISION,policy_id=POLICY_ID,scope_body_sha256=SCOPE_SHA,
                       reviewed_at=reviewed_at,expires_at=expires_at,reviewed_by=None,reason=None,entries=entries)


def _validate(ledger,scope,now=None):
    if (not isinstance(ledger,dict) or ledger.get('schema_version')!=1 or ledger.get('state')!='ACTIVE'
            or ledger.get('decision')!=DECISION or ledger.get('policy_id')!=POLICY_ID
            or ledger.get('scope_body_sha256')!=SCOPE_SHA
            or not isinstance(ledger.get('entries'),list) or not ledger['entries']):
        raise ValueError('legacy acceptance must be explicit ACTIVE, correctly scoped, and nonempty')
    for field in ('reviewed_by','reason'):
        if not isinstance(ledger.get(field),str) or not ledger[field].strip():raise ValueError(f'legacy {field} missing')
    reviewed,expires=_term(ledger.get('reviewed_at'),ledger.get('expires_at'))
    now=now or datetime.now(timezone.utc)
    if reviewed>now or expires<=now:raise ValueError('legacy acceptance future-dated or expired')
    accepted={};originals=set();keys=set()
    for entry in ledger['entries']:
        if not isinstance(entry,dict):raise ValueError('legacy acceptance entry invalid')
        ident=entry.get('original_review_id')
        if not isinstance(ident,str) or ident not in scope or ident in originals:
            raise ValueError('legacy original review ID unknown or duplicated')
        expected=_entry(ident,scope[ident],ledger['expires_at'])
        if entry!=expected or entry['accepted_review_id'] in accepted or entry['master_key'] in keys:
            raise ValueError('legacy acceptance record or relation binding mismatch')
        originals.add(ident);keys.add(entry['master_key']);accepted[entry['accepted_review_id']]=entry
    return accepted


def _read_csv(path,compressed=False):
    body=path.read_bytes()
    if compressed:body=gzip.decompress(body)
    reader=csv.DictReader(io.StringIO(body.decode('utf-8-sig'),newline=''),strict=True)
    rows=list(reader)
    if not reader.fieldnames or any(None in r or None in r.values() for r in rows):raise ValueError('invalid legacy projection CSV')
    return reader.fieldnames,rows


def _display(root,rows):
    """Inspect serialized consumers independently, including the old 4-column UI."""
    fields,listed=_read_csv(Path(root)/'data/dashboard/list.csv')
    if fields!=['受取人名','リスクタイプ','状態','リスク度']:raise ValueError('legacy list schema mismatch')
    actual=Counter(tuple(r[f] for f in fields) for r in listed)
    visible=[r for r in rows if not is_trailing_unknown_artifact(canonical_display_name(r['display_name']))]
    expected=Counter((canonical_display_name(r['display_name']),r['risk_type'],r['status'],r['risk_level']) for r in visible)
    if expected!=actual:raise ValueError('legacy search list is not the complete current projection')
    fields,screened=_read_csv(Path(root)/'data/dashboard/screening.csv.gz',True)
    if fields!=['match_key','secondary_key','受取人名','出所','カテゴリ','リスクタイプ','リスク度','状態','要確認']:
        raise ValueError('legacy screening schema mismatch')
    actual=Counter(tuple(r[f] for f in fields) for r in screened)
    expected=Counter((r['match_key'].strip(),secondary_screening_key(canonical_display_name(r['display_name'])),
                      canonical_display_name(r['display_name']),r['sources'],r['categories'],r['risk_type'],
                      r['risk_level'],r['status'],r['review_flag']) for r in visible if r['status'].strip()=='有効')
    if actual!=expected:raise ValueError('legacy screening is not the complete current projection')


def _row_bindings(rows,accepted,scope,ledger):
    bykey={}
    for row in rows:bykey.setdefault(row['match_key'],[]).append(row)
    expected=[]
    for entry in accepted.values():
        row=annotate(scope[entry['original_review_id']]['finding']['master'],entry['classification'],ledger['expires_at'])
        if bykey.get(entry['master_key'])!=[row]:raise ValueError('legacy candidate annotation missing, changed, or duplicated')
        expected.append(row)
    if {r['match_key'] for r in rows if MARKER in r.get('review_flag','')}!={r['match_key'] for r in expected}:
        raise ValueError('legacy annotations exist outside active acceptance')
    return expected


def apply(root,report,triage,ledger,scope,*,now=None):
    """Keep all findings visible; release only exact old findings, never vetoes."""
    from .integrity_audit import _finish
    result=copy.deepcopy(report)
    try:
        accepted=_validate(ledger,scope,now)
        _,rows=_read_csv(Path(root)/'data/master/master.csv')
        marked=_row_bindings(rows,accepted,scope,ledger)
        if len(triage['items'])!=len(report['findings']):raise ValueError('legacy triage length mismatch')
        matches={}
        for n,(finding,item) in enumerate(zip(report['findings'],triage['items'])):
            ident=item['review_id']
            if ident not in accepted:continue
            entry=accepted[ident]
            if (item['finding']!=finding or item['classification']!=entry['classification']
                    or item['master_sha256']!=entry['accepted_master_sha256'] or item['checked_source']!='OFAC'
                    or finding['kind'] not in {'EXTRA_REGISTRATION','WEAK_ALIAS_ACTIVE'} or ident in matches):
                raise ValueError('legacy current finding mismatch')
            matches[ident]=n
        if set(matches)!=set(accepted):raise ValueError('legacy accepted relation missing or changed; fresh review required')
    except (ValueError,OSError,KeyError,TypeError,csv.Error,EOFError,zlib.error) as exc:
        result['findings'].append(dict(kind='LEGACY_ACCEPTANCE_INVALID',detail=str(exc),blocking=True))
        return _finish(result)
    try:
        _display(root,rows)
    except (ValueError,OSError,KeyError,csv.Error,EOFError,zlib.error) as exc:
        result['findings'].append(dict(kind='LEGACY_DISPLAY_MISMATCH',detail=str(exc),blocking=True))
        return _finish(result)
    for ident,n in matches.items():
        result['findings'][n].update(blocking=False,legacy_accepted=True,uncertainty_status=accepted[ident]['classification'],
                                     legacy_review_id=ident,legacy_expires_at=ledger['expires_at'])
    result['legacy_acceptance']=dict(policy_id=POLICY_ID,count=len(matches),reviewed_by=ledger['reviewed_by'],
        reviewed_at=ledger['reviewed_at'],expires_at=ledger['expires_at'],reason=ledger['reason'],verified=False)
    return _finish(result)


def validate_for_export(root,rows,*,now=None):
    root=Path(root);path=root/LEDGER_PATH
    if not path.exists():
        if any(MARKER in r.get('review_flag','') for r in rows):raise ValueError('legacy export has no active acceptance')
        return
    scope=load_scope(root);ledger=json.loads(path.read_bytes());accepted=_validate(ledger,scope,now)
    _row_bindings(rows,accepted,scope,ledger)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--prepare',action='store_true',required=True)
    parser.add_argument('--reviewed-at',required=True);parser.add_argument('--expires-at',required=True)
    parser.add_argument('--review-id',action='append');parser.add_argument('--out-dir',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        root=args.root.resolve();out=args.out_dir.resolve()
        if out==root or any(out.is_relative_to(root/p) for p in ('data','dist','.git')):
            raise ValueError('candidate output must be separate from business data')
        scope=load_scope(root);fields,rows=_read_csv(root/'data/master/master.csv')
        rows,ledger=prepare(rows,scope,args.reviewed_at,args.expires_at,args.review_id)
        out.mkdir(parents=True,exist_ok=False)
        with (out/'candidate-master.csv').open('x',encoding='utf-8',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(rows)
        (out/'draft-legacy-acceptances.json').write_text(json.dumps(ledger,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
        print(json.dumps(dict(state='DRAFT',annotated=len(ledger['entries']),production_changes=0),ensure_ascii=False))
        return 0
    except (OSError,ValueError,KeyError,TypeError,csv.Error,EOFError,zlib.error) as exc:
        print(f'LEGACY PREPARATION BLOCKED: {exc}');return 1


if __name__=='__main__':raise SystemExit(main())
