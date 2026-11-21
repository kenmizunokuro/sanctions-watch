"""Hash-pinned 30-day renewal for historical vessel and alias retentions.

Original historical approval bundles and one-time application receipts remain
immutable. This module authorizes only an exact, bounded term extension of
those already-approved records while preserving all static and live OFAC
identity checks.
"""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path

from . import integrity_triage as T
from . import master as M
from .integrity_decisions import _date

RENEWAL = Path('data/review/approved_integrity_renewal_20261121.json')
RENEWAL_SHA256 = '52ffacca6bb2366ade22b7b904ba098e0a789076a2d47ac35e6c2603cdc7d6bb'
DECISION = 'APPROVED_RENEW_HISTORICAL_RETENTIONS'


def load_renewal(root: Path) -> dict | None:
    root = Path(root).resolve()
    path = root / RENEWAL
    if not path.exists():
        return None
    if not path.resolve().is_relative_to(root / 'data/review'):
        raise ValueError('integrity renewal escapes review directory')
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != RENEWAL_SHA256:
        raise ValueError('integrity renewal hash changed')
    bundle = json.loads(body)
    if (not isinstance(bundle, dict) or bundle.get('schema_version') != 1
            or bundle.get('decision') != DECISION):
        raise ValueError('integrity renewal schema or decision invalid')
    reviewed = _date(bundle.get('reviewed_at'))
    expires = _date(bundle.get('expires_at'))
    if not timedelta(0) < expires - reviewed <= timedelta(days=30):
        raise ValueError('integrity renewal term must be positive and at most 30 days')
    return bundle


def vessel_decision(root: Path, case: dict, base_approval_sha256: str) -> dict:
    renewal = load_renewal(root)
    base = case['retention_decision']
    if renewal is None:
        return base
    section = renewal.get('vessel_history')
    if (not isinstance(section, dict)
            or section.get('base_approval_sha256') != base_approval_sha256):
        raise ValueError('vessel renewal base approval hash mismatch')
    entry = (section.get('cases') or {}).get(case['master_key'])
    if not isinstance(entry, dict) or not isinstance(entry.get('retention_decision'), dict):
        raise ValueError('vessel renewal case missing')
    renewed = entry['retention_decision']
    for field in ('review_id', 'decision', 'reason', 'reviewed_evidence_sha256', 'vessel_history'):
        if renewed.get(field) != base.get(field):
            raise ValueError(f'vessel renewal altered immutable field: {field}')
    if (renewed.get('reviewed_at') != renewal['reviewed_at']
            or renewed.get('expires_at') != renewal['expires_at']):
        raise ValueError('vessel renewal timestamps mismatch')
    return renewed


def alias_state(root: Path, base_bundle: dict, base_approval_sha256: str) -> tuple[dict, dict, str, str]:
    renewal = load_renewal(root)
    if renewal is None:
        return (
            base_bundle['after'],
            base_bundle['retention_decision'],
            base_bundle['reviewed_at'],
            base_bundle['expires_at'],
        )
    key = base_bundle['master_key']
    entry = (renewal.get('alias_history') or {}).get(key)
    if (not isinstance(entry, dict)
            or entry.get('base_approval_sha256') != base_approval_sha256):
        raise ValueError('alias renewal base approval hash mismatch')
    after = entry.get('after')
    decision = entry.get('retention_decision')
    if not isinstance(after, dict) or not isinstance(decision, dict):
        raise ValueError('alias renewal scope missing')
    if T.digest(after) != entry.get('after_sha256'):
        raise ValueError('alias renewal row digest mismatch')
    base_after = base_bundle['after']
    for field in M.FIELDS:
        if field != 'remark' and after.get(field) != base_after.get(field):
            raise ValueError(f'alias renewal altered immutable row field: {field}')
    expected_remark = base_after['remark'].replace(
        f"保持期限UTC:{base_bundle['expires_at']}",
        f"保持期限UTC:{renewal['expires_at']}",
    )
    if expected_remark == base_after['remark'] or after.get('remark') != expected_remark:
        raise ValueError('alias renewal remark mismatch')
    base_decision = base_bundle['retention_decision']
    for field in ('decision', 'reason', 'alias_history'):
        if decision.get(field) != base_decision.get(field):
            raise ValueError(f'alias renewal altered immutable decision field: {field}')
    if (decision.get('reviewed_at') != renewal['reviewed_at']
            or decision.get('expires_at') != renewal['expires_at']):
        raise ValueError('alias renewal timestamps mismatch')
    return after, decision, renewal['reviewed_at'], renewal['expires_at']
