"""Exact vessel name history authority; independent current identity checks.

A historic name is a retained screening string, not a current official name.
No network calls, fuzzy matching, implicit approval or automatic delisting.
"""
from __future__ import annotations
import gzip,hashlib,json,re,zlib
from pathlib import Path
from . import integrity_readers as R,integrity_notice_context as N
from .normalize import match_key

APPROVAL=Path('data/review/approved_vessel_history_20261109.json')
APPROVAL_SHA256='7ad33e0b50ae788ba78348853513a3f2b5adca5f66f9a5af6b93cdeb66d0a273'
MARKER='OFAC_VESSEL_HISTORY_V1:'
KEYS={'carlotac','giralt','nedas'}
SDN_URL='https://sanctionslistservice.ofac.treas.gov/api/download/SDN.CSV'

def approval(root):
    path=Path(root)/APPROVAL
    if not path.resolve().is_relative_to(Path(root).resolve()/'data/review'):
        raise ValueError('vessel approval escapes review directory')
    body=path.read_bytes()
    if hashlib.sha256(body).hexdigest()!=APPROVAL_SHA256:raise ValueError('vessel approval hash changed')
    bundle=json.loads(body)
    if (bundle.get('schema_version')!=1 or bundle.get('decision')!='APPROVED_RETAIN_HISTORICAL_VESSEL_NAMES'
            or len(bundle['cases'])!=3 or {c['master_key'] for c in bundle['cases']}!=KEYS):
        raise ValueError('vessel approval scope changed')
    return bundle

def _body(root,document,directory):
    root=Path(root).resolve();base=root/directory;rel=Path(document['raw_path']);path=root/rel
    if rel.is_absolute() or '..' in rel.parts or not base.resolve().is_relative_to(root) or not path.resolve().is_relative_to(base.resolve()):
        raise ValueError('vessel source escapes evidence directory')
    raw=path.read_bytes()
    try:body=gzip.decompress(raw) if path.suffix=='.gz' else raw
    except (EOFError,zlib.error,OSError) as exc:raise ValueError('vessel source unreadable') from exc
    if hashlib.sha256(body).hexdigest()!=(document.get('source_hash') or document.get('sha256')):
        raise ValueError('vessel source hash mismatch')
    return body

def _vessel(records,case):
    matches=[r for r in records if r['source_id']==case['party_id']]
    if len(matches)!=1:raise ValueError('vessel Party ID missing or ambiguous')
    row=matches[0]['raw_record']
    if row[2]!='vessel' or re.findall(r'Vessel Registration Identification IMO (\d{7})',row[11])!=[case['imo']]:
        raise ValueError('vessel type or IMO changed')
    return matches[0]

def static_evidence(root,case,bundle):
    notice=bundle['notice']
    if notice['source_url']!='https://ofac.treasury.gov/recent-actions/20261105':raise ValueError('unapproved notice URL')
    records=N.read_records(_body(root,notice,'docs/evidence/vessel-name-history'))
    matches=[r for r in records if r['record_number']==case['notice_record_number']]
    if len(matches)!=1:raise ValueError('notice record missing')
    record=matches[0];text=record['record']
    if (record['heading']!="The following changes have been made to OFAC's SDN List:"
            or hashlib.sha256(text.encode()).hexdigest()!=case['notice_record_sha256']):
        raise ValueError('notice record changed')
    parts=text.split(' -to- ')
    if (len(parts)!=2 or not (parts[0].startswith(case['old_name']+' ') or f"(f.k.a. {case['old_name']})" in parts[0])
            or not parts[1].startswith(case['replacement_name']+' ')
            or any(re.findall(r'Vessel Registration Identification IMO (\d{7})',p)!=[case['imo']] for p in parts)):
        raise ValueError('notice does not prove same-IMO name transition')
    historical=[]
    for role in ('primary','alias'):
        document=bundle['historical'][role]
        if document['source']!='ofac_sdn' or document['role']!=role:raise ValueError('historical source role changed')
        rows=R.read_classic(_body(root,document,'docs/evidence/integrity-archives'),'SDN',alias=role=='alias')
        if role=='primary':_vessel(rows,case)
        historical.extend(rows)
    if not any(r['source_id']==case['party_id'] and match_key(r['name'])==case['master_key'] for r in historical):
        raise ValueError('historical official name/Party binding missing')
    return dict(notice_url=notice['source_url'],notice_sha256=notice['source_hash'],notice_record_sha256=case['notice_record_sha256'])

def verify_decision(root,report,decision):
    bundle=approval(root);link=decision.get('vessel_history')
    if not isinstance(link,dict):raise ValueError('vessel decision identity missing')
    cases=[c for c in bundle['cases'] if c['master_key']==link.get('master_key')]
    if len(cases)!=1 or decision!=cases[0]['retention_decision']:raise ValueError('vessel decision scope changed')
    case=cases[0];facts=static_evidence(root,case,bundle)
    documents=[d for d in report.get('documents',[]) if d.get('source')=='ofac_sdn' and d.get('role')=='primary']
    if len(documents)!=1 or documents[0]['source_url']!=SDN_URL:raise ValueError('current official SDN document missing')
    document=documents[0];current=_vessel(R.read_classic(_body(root,document,'data/raw/ofac_sdn'),'SDN'),case)
    if current['name']!=case['replacement_name']:raise ValueError('current vessel name changed; re-review required')
    return dict(link,**facts,current_source_url=document['source_url'],current_source_sha256=document['sha256'],name_status='historical_not_current_name')
