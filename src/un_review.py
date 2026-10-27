"""Explicit snapshot-bound UN review and source-scoped master application."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import dashboard as D, master as M, un_sources as U, un_watch as W
from .fetch import read_raw
from .persistence import FileWrite, atomic_replace_many
from .un_lock import locked


def master_hash(root: Path) -> str:
    path = root / 'data/master/master.csv'
    return hashlib.sha256(path.read_bytes() if path.exists() else b'').hexdigest()


def _operator(name, when, note):
    if not name.strip() or not note.strip() or when.tzinfo is None:
        raise ValueError('reviewer/operator, reason and timezone-aware timestamp required')
    return when.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _current(root, event_id, source_hash, when):
    state = W.load(root)
    if (state.get('current_event_id') != event_id or state.get('snapshot_hash') != source_hash
            or state.get('xml_status') == 'error' or state.get('press_status') == 'error'):
        raise ValueError('stale event/hash or incomplete UN observation')
    observed = datetime.fromisoformat(state['xml_last_success_at'].replace('Z', '+00:00'))
    if not 0 <= (when - observed).total_seconds() <= 5400:
        raise ValueError('UN observation must be within 90 minutes of review/application')
    snapshot = json.loads((root / W.SNAPSHOT_PATH).read_text(encoding='utf-8'))
    if U.digest(snapshot) != state['snapshot_digest']:
        raise ValueError('UN snapshot corrupted')
    body = read_raw(root / state['documents']['xml']['raw_path'])
    if hashlib.sha256(body).hexdigest() != source_hash:
        raise ValueError('UN original hash mismatch')
    parsed = U.parse_xml(body)
    if parsed['parties'] != snapshot['parties']:
        raise ValueError('UN snapshot does not match official original')
    event = next(e for e in state['events'] if e['event_id'] == event_id)
    return state, snapshot, event


@locked
def approve(root: Path, *, event_id: str, source_hash: str, reviewer: str,
            when: datetime, note: str, evidence_url: str) -> dict:
    at = _operator(reviewer, when, note)
    U.validate_un_url(evidence_url)
    state, snapshot, event = _current(root, event_id, source_hash, when)
    if event['status'] != 'PENDING_REVIEW':
        raise ValueError('only the current pending snapshot can be approved')
    event.update(status='APPROVED', reviewer=reviewer, reviewed_at=at, review_note=note,
                 evidence_url=evidence_url, approved_snapshot_digest=state['snapshot_digest'],
                 approved_application_digest=U.digest(event['application_diff']))
    atomic_replace_many(W.projections(root, state, now=when))
    return event


def _refresh(state):
    state['xml_status'] = ('error' if any(f['source'] == 'un_xml' for f in state.get('failures', []))
                           else 'review_required' if any(e['status'] in {'PENDING_REVIEW', 'APPROVED'}
                                                        for e in state['events']) else 'unchanged')
    state['press_status'] = ('error' if state['coverage_gaps'] or any(f['source'].startswith('press:')
                             for f in state.get('failures', [])) else 'review_required'
                             if any(n['status'] == 'PENDING_REVIEW' for n in state['notices'].values()) else 'unchanged')


@locked
def apply(root: Path, *, event_id: str, source_hash: str, expected_master_hash: str,
          operator: str, when: datetime, note: str) -> dict:
    at = _operator(operator, when, note)
    state, snapshot, event = _current(root, event_id, source_hash, when)
    generation = hashlib.sha256((root / W.STATE_PATH).read_bytes()).hexdigest()
    if event['status'] != 'APPROVED' or event.get('approved_snapshot_digest') != state['snapshot_digest']:
        raise ValueError('current exact snapshot has not been approved')
    if (event.get('approved_application_digest') != U.digest(event['application_diff'])
            or event['application_base_event_id'] != state.get('applied_event_id', '')):
        raise ValueError('application delta changed since approval')
    before = master_hash(root)
    if before != expected_master_hash:
        raise ValueError('master changed since confirmation')
    rows = M.load(root / 'data/master/master.csv')
    records = [dict(name=n['name'], source_id=ref, category=p['committee'])
               for ref, p in snapshot['parties'].items() for n in p['names'] if not n['weak']]
    diff = M.merge(rows, records, '国連', ts=int(when.timestamp() * 1000),
                   delist=bool(state.get('applied_event_id')), report_missing=False)
    # Generate and validate all derived outputs before publishing the new master.
    with tempfile.TemporaryDirectory() as directory:
        stage = Path(directory)
        M.save(rows, stage / 'master.csv')
        D.write_list(stage, rows)
        D.write_screening_gzip(stage, rows)
        diff_rows = M.diff_rows([diff])
        initial_import = not state.get('applied_event_id')
        if initial_import:
            for row in diff_rows:
                if row[1] == '追加':
                    row[1] = '初回同期（国連）'
        with (stage / 'diff.csv').open('w', encoding='utf-8', newline='') as file:
            writer = csv.writer(file, lineterminator='\n')
            writer.writerow(['出所', '種別', '受取人名', '変更前', '変更後'])
            writer.writerows(diff_rows)
        after = hashlib.sha256((stage / 'master.csv').read_bytes()).hexdigest()
        counts = dict(diff.counts)
        if initial_import:
            counts.update(追加=0, 初回同期=len(diff.added))
        event.update(status='APPLIED', applied_at=at, operator=operator, apply_note=note,
                     master_before_sha256=before, master_after_sha256=after, master_counts=counts)
        state['applied_event_id'] = event_id
        _refresh(state)
        writes = W.projections(root, state, now=when)
        outputs = {'master.csv': 'data/master/master.csv', 'diff.csv': 'data/diff/latest.csv',
                   'data/dashboard/list.csv': 'data/dashboard/list.csv',
                   'data/dashboard/screening.csv.gz': 'data/dashboard/screening.csv.gz'}
        for source, target in outputs.items():
            body = (stage / source).read_bytes()
            writes.append(FileWrite(root / target, lambda p, b=body: p.write_bytes(b)))
        writes.append(FileWrite(root / 'data/dashboard/changes.csv',
                                lambda p: D.prepend_change_rows(p, diff_rows, when=D._jst(at)), seed_existing=True))
        audit_path = root / 'data/un/apply_audit.jsonl'
        audit_record = dict(event_id=event_id, source_hash=source_hash, operator=operator, applied_at=at,
                            reviewer=event['reviewer'], reviewed_at=event['reviewed_at'],
                            master_before_sha256=before, master_after_sha256=after, counts=counts)
        def write_audit(path):
            with path.open('a', encoding='utf-8') as file:
                file.write(json.dumps(audit_record, ensure_ascii=False, sort_keys=True) + '\n')
        writes.append(FileWrite(audit_path, write_audit, seed_existing=True))
        if master_hash(root) != before:
            raise ValueError('master changed during application')
        if hashlib.sha256((root / W.STATE_PATH).read_bytes()).hexdigest() != generation:
            raise ValueError('UN generation changed during application')
        atomic_replace_many(writes)
    return dict(applied_event_id=event_id, master_before_sha256=before, master_after_sha256=after, counts=counts)


@locked
def review_notice(root: Path, *, event_id: str, index_hash: str, reviewer: str,
                  when: datetime, note: str) -> dict:
    at = _operator(reviewer, when, note)
    state = W.load(root)
    notice = state['notices'].get(event_id)
    if not notice or notice['index_hash'] != index_hash or notice['status'] != 'PENDING_REVIEW':
        raise ValueError('notice event/hash is not pending')
    notice.update(status='REVIEWED', reviewer=reviewer, reviewed_at=at, review_note=note)
    _refresh(state)
    atomic_replace_many(W.projections(root, state, now=when))
    return notice


@locked
def review_coverage(root: Path, *, committee: str, reviewer: str, when: datetime, note: str):
    at = _operator(reviewer, when, note)
    state = W.load(root)
    gap = state['coverage_gaps'].pop(committee)
    state.setdefault('coverage_reviews', []).append(dict(gap, committee=committee, reviewer=reviewer,
                                                         reviewed_at=at, review_note=note))
    _refresh(state)
    atomic_replace_many(W.projections(root, state, now=when))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['status', 'approve', 'apply', 'notice-review', 'coverage-review'])
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
    for name in ['event-id', 'source-hash', 'reviewer', 'operator', 'note', 'evidence-url',
                 'expected-master-hash', 'index-hash', 'committee']:
        parser.add_argument('--' + name, default='')
    parser.add_argument('--at', default=datetime.now(timezone.utc).isoformat())
    args = parser.parse_args()
    when = datetime.fromisoformat(args.at.replace('Z', '+00:00'))
    if args.command == 'status':
        state = W.load(args.root)
        result = dict(current_event_id=state.get('current_event_id'), snapshot_hash=state.get('snapshot_hash'),
                      master_hash=master_hash(args.root), events=state['events'],
                      review_count=len(W._review_rows(state)))
    elif args.command == 'approve':
        result = approve(args.root, event_id=args.event_id, source_hash=args.source_hash,
                         reviewer=args.reviewer, when=when, note=args.note, evidence_url=args.evidence_url)
    elif args.command == 'apply':
        result = apply(args.root, event_id=args.event_id, source_hash=args.source_hash,
                       expected_master_hash=args.expected_master_hash, operator=args.operator, when=when, note=args.note)
    elif args.command == 'notice-review':
        result = review_notice(args.root, event_id=args.event_id, index_hash=args.index_hash,
                               reviewer=args.reviewer, when=when, note=args.note)
    else:
        result = review_coverage(args.root, committee=args.committee, reviewer=args.reviewer, when=when, note=args.note)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
