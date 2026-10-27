"""Explicit, record-bound retention approvals. No baseline/grandfather waiver.

Approval authority comes from the human-reviewed repository file, as for the
existing OFAC removal workflow. This module validates scope, not human identity.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timezone


class DecisionError(ValueError):
    pass


_ALLOWED={'EXTRA_REGISTRATION','WEAK_ALIAS_ACTIVE'}


def _date(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z',value):
        raise DecisionError('承認日時はUTC Z形式が必要')
    try:
        return datetime.strptime(value,'%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise DecisionError('承認日時が不正') from exc


def apply(report,triage,ledger,*,now=None):
    """Copy the report; only an eligible exact review ID may become nonblocking.

    Expired/stale entries never release current findings. A resolved old finding
    does not manufacture a new defect. Unknown/new defects keep their veto.
    """
    if not isinstance(ledger,dict) or ledger.get('schema_version')!=1 or not isinstance(ledger.get('entries'),list):
        raise DecisionError('保持承認ファイルの構造が不正')
    now=now or datetime.now(timezone.utc)
    entries={}
    for row in ledger['entries']:
        if not isinstance(row,dict):raise DecisionError('保持承認行が不正')
        ident=row.get('review_id','')
        if not isinstance(ident,str) or not re.fullmatch('[a-f0-9]{64}',ident) or ident in entries:
            raise DecisionError('review_id不正/重複。ワイルドカードは使用不可')
        if row.get('decision')!='APPROVED_RETAIN':raise DecisionError('明示的なAPPROVED_RETAINが必要')
        for field in ('reviewed_by','reason'):
            if not isinstance(row.get(field),str) or not row[field].strip():raise DecisionError(f'{field}が空')
        if not re.fullmatch('[a-f0-9]{64}',str(row.get('reviewed_evidence_sha256',''))):
            raise DecisionError('承認時の根拠hashが必要')
        reviewed=_date(row.get('reviewed_at'));expires=_date(row.get('expires_at'))
        if expires<=reviewed:raise DecisionError('承認期限が承認日以前')
        entries[ident]=(row,reviewed,expires)
    result=copy.deepcopy(report)
    result['retention_review']=[]
    matched=set()
    if len(triage['items'])!=len(result['findings']):raise DecisionError('triageと監査findingの件数不一致')
    for finding,item in zip(result['findings'],triage['items']):
        # Never use a triage finding from a different report to release this one.
        if item['finding']!=finding:raise DecisionError('triageと監査findingの内容不一致')
        ident=item['review_id']
        if ident not in entries:continue
        matched.add(ident);row,reviewed,expires=entries[ident]
        state='APPLIED'
        if (finding['kind'] not in _ALLOWED or not item['retention_eligible']
                or (finding.get('checked_source') or (finding.get('official') or {}).get('source'))!='OFAC'):
            state='INELIGIBLE'
        elif reviewed>now:state='FUTURE_REVIEW'
        elif expires<=now:state='EXPIRED'
        result['retention_review'].append(dict(review_id=ident,status=state,reviewed_by=row['reviewed_by'],
                                               reviewed_at=row['reviewed_at'],expires_at=row['expires_at'],reason=row['reason'],
                                               reviewed_evidence_sha256=row['reviewed_evidence_sha256'],
                                               current_evidence_sha256=item['evidence_sha256']))
        if state=='APPLIED':
            finding.update(blocking=False,retention_approved=True,retention_review_id=ident)
    result['retention_review'].extend(dict(review_id=ident,status='STALE_OR_RESOLVED') for ident in sorted(set(entries)-matched))
    return result
