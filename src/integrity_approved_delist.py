"""Recognize six applied delistings against one older, archived MOF snapshot.

This only annotates audit findings. It neither applies delistings nor waives a
current source conflict. Authority and row bindings are exact and read-only.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from .approved_legacy_delist import APPROVAL, APPROVAL_SHA256, KEYS, RECEIPT

# The published receipt is immutable application evidence, not a requirement that
# today's entire master still equals the historical receipt's master hash.
RECEIPT_SHA256 = 'e187b47cc8557e76ed44285d0bea2556afba84b6050c3f5ae43e0dcc37c45a5d'
HISTORICAL_SOURCE_SHA256 = 'f8e1038419ab0146cbce89ae9fa7b5f9d298548c8bca4953f970c01f87b3f7bc'
HISTORICAL_SOURCE_URL = ('https://www.mof.go.jp/policy/international_policy/gaitame_kawase/'
                         'gaitame/economic_sanctions/shisantouketsu20261021.csv')
MASTER = 'data/master/master.csv'
CLASSIFICATION = 'APPROVED_DELIST_HISTORICAL_SOURCE'

# SHA256 of each complete official witness from the validated Oct 21 archive,
# using canonical JSON, excluding only raw_path (storage location). This binds
# name, Party number, category, name field, locator, quality and raw source row;
# identical names under another Party or a new document cannot inherit approval.
OFFICIAL_SHA256 = {
    'abuahmadakhlaq': '5037b18a7113ced458a5a84feb024af152683a1c33befb1c3e2ececbdac53507',
    'abuahmadal-shami': 'c4c5670f8a7a1ebd84d8476725482ca17385fbb6f63e2c1c052a6a4ca48cf92a',
    'abual-darda': 'caa50bba5bb39089c66810e5d4e9440293c372baf6602840a42856bbb7f8394b',
    'bassamahmadal-hasri': '315bb59f1faf138d9856f45c70a7a72c691271c28d7f5a56db19c9f3ee1e824b',
    'bassamahmadhusari': '239731e441622b819d54bf4599e4ae51c01f80f10b621ee92320f09d95e7574d',
    'iyadnazmisalihkhalil': '7eb198cff1919f567a407c35e37308184a904476955314fdf24995e5412e0911',
}


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _digest(value: dict) -> str:
    return _sha(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())


def _read(root: Path, relative: str | Path) -> bytes:
    path = root / relative
    if not path.resolve().is_relative_to(root / 'data'):
        raise ValueError(f'approved delist evidence escapes data: {relative}')
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ValueError(f'approved delist evidence unavailable: {relative}') from exc


def _authority(root: Path) -> tuple[dict, dict, dict]:
    approval_body, receipt_body = _read(root, APPROVAL), _read(root, RECEIPT)
    if _sha(approval_body) != APPROVAL_SHA256:
        raise ValueError('approved delist approval hash mismatch')
    if _sha(receipt_body) != RECEIPT_SHA256:
        raise ValueError('approved delist application receipt hash mismatch')
    approval, receipt = json.loads(approval_body), json.loads(receipt_body)
    cases = {case['match_key']: case for case in approval['cases']}
    if (approval.get('schema_version') != 1 or approval.get('decision') != 'APPROVED_DELIST'
            or len(approval['cases']) != 6 or set(cases) != KEYS
            or set(OFFICIAL_SHA256) != KEYS):
        raise ValueError('approved delist authority scope changed')
    if (receipt.get('schema_version') != 1 or receipt.get('status') != 'APPLIED'
            or type(receipt.get('count')) is not int or receipt['count'] != 6
            or receipt.get('approval_sha256') != APPROVAL_SHA256
            or receipt.get('approved_at') != approval['approved_at']):
        raise ValueError('approved delist application receipt is not bound to the six-name approval')
    for key, case in cases.items():
        after = case['after']
        if (after['match_key'] != key or after['status'] != '無効'
                or after['sources'] or after['categories']
                or after['review_flag'] != 'APPROVED_LEGACY_DELIST_20261108'
                or _digest(after) != case['after_sha256']):
            raise ValueError(f'approved delist after-row binding invalid: {key}')
    return cases, receipt, {APPROVAL.as_posix(): _sha(approval_body), RECEIPT.as_posix(): _sha(receipt_body)}


def apply(root: Path, report: dict) -> dict:
    """Return a copied report with only exact historical conflicts nonblocking.

    The audit must already have decoded and hash-validated its documents. Missing
    application evidence gives no exception; corrupt authority raises ValueError
    for the audit entrypoint to record as a blocking authority error. All other
    findings, including upstream/schema failures, retain their original content.
    """
    from .integrity_audit import _csv, _finish

    root = Path(root).resolve()
    result = copy.deepcopy(report)
    if not (root / RECEIPT).exists():
        return _finish(result)
    cases, receipt, authority_inputs = _authority(root)
    result.setdefault('inputs', {}).update(authority_inputs)
    documents = [doc for doc in result.get('documents', []) if doc.get('source') == 'mof']
    if len(documents) != 1:
        return _finish(result)
    document = documents[0]
    if (document.get('role') != 'mof' or document.get('sha256') != HISTORICAL_SOURCE_SHA256
            or document.get('source_url') != HISTORICAL_SOURCE_URL
            or document.get('bytes') != 3327198 or document.get('name_count') != 15693
            or not isinstance(document.get('raw_path'), str) or not document['raw_path']):
        return _finish(result)

    # Bind report witnesses to this audited master, while allowing unrelated
    # legitimate changes since the original application receipt was published.
    master_body = _read(root, MASTER)
    if result['inputs'].get(MASTER) != _sha(master_body):
        raise ValueError('approved delist audit master input hash mismatch')
    actual = {}
    for row in _csv(root / MASTER, ['match_key', 'display_name', 'status', 'sources', 'categories']):
        actual.setdefault(row['match_key'], []).append(row)

    for finding in result['findings']:
        if finding.get('kind') not in {'STATUS_MISMATCH', 'SOURCE_MISMATCH'}:
            continue
        master, official = finding.get('master'), finding.get('official')
        if not isinstance(master, dict) or not isinstance(official, dict):
            continue
        key = master.get('match_key')
        case = cases.get(key)
        if (case is None or master != case['after'] or _digest(master) != case['after_sha256']
                or actual.get(key) != [case['after']]
                or official.get('raw_path') != document['raw_path']):
            continue
        witness = {k: v for k, v in official.items() if k != 'raw_path'}
        if _digest(witness) != OFFICIAL_SHA256[key]:
            continue
        finding['blocking'] = False
        finding['approved_delist'] = dict(
            classification=CLASSIFICATION,
            explanation='承認・反映済みの掲載終了。旧財務省snapshotとの不一致を保持。現行掲載の免除ではない。',
            approval_path=APPROVAL.as_posix(), approval_sha256=APPROVAL_SHA256,
            application_receipt_path=RECEIPT.as_posix(), application_receipt_sha256=RECEIPT_SHA256,
            applied_at=receipt['applied_at'], master_after_sha256=case['after_sha256'],
            historical_source_sha256=HISTORICAL_SOURCE_SHA256,
        )
    return _finish(result)
