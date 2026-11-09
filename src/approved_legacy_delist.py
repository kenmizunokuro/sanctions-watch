"""Materialize the six exact user-approved legacy name delistings, once.

This is a fixed approval migration, not a source poll or a general gate waiver.
No official snapshots, source candidates or unrelated master rows are promoted.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import re
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import dashboard as D, integrity_legacy as L, master as M
from . import mof_legacy_review as V
from .persistence import FileWrite, atomic_replace_many

ROOT = Path(__file__).resolve().parent.parent
APPROVAL = Path('data/review/approved_legacy_delist_20261108.json')
APPROVAL_SHA256 = '223a3be4ed97ea2e75c0a059cf8f286a09ae22f76f7267eae851cd57dc093c95'
RECEIPT = Path('data/monitoring/approved_legacy_delist_20261108_applied.json')
KEYS = {'abuahmadakhlaq', 'abuahmadal-shami', 'abual-darda',
        'bassamahmadal-hasri', 'bassamahmadhusari', 'iyadnazmisalihkhalil'}
TARGETS = ('data/master/master.csv', L.LEDGER_PATH, 'data/dashboard/list.csv',
           'data/dashboard/screening.csv.gz', 'data/dashboard/changes.csv',
           'data/diff/latest.csv', V.OUT.as_posix())
DIFF_COLS = ['出所', '種別', '受取人名', '変更前', '変更後']


def _json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def apply(root: Path = ROOT, *, now: datetime | None = None, run_url: str = '') -> dict:
    root = root.resolve()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or (run_url and not re.fullmatch(
            r'https://github\.com/[\w.-]+/[\w.-]+/actions/runs/[0-9]+', run_url)):
        raise ValueError('invalid application timestamp or run URL')
    body = (root / APPROVAL).read_bytes()
    if hashlib.sha256(body).hexdigest() != APPROVAL_SHA256:
        raise ValueError('approved legacy delisting artifact hash changed')
    approval = json.loads(body)
    cases = {c['match_key']: c for c in approval['cases']}
    if (approval['decision'] != 'APPROVED_DELIST' or set(cases) != KEYS
            or len(approval['cases']) != 6 or V._date(approval['approved_at']) > now):
        raise ValueError('approved six-name scope or date changed')
    rows = V._read_rows((root / 'data/master/master.csv').read_text(encoding='utf-8'))
    ledger = json.loads((root / L.LEDGER_PATH).read_bytes())
    scope = L.load_scope(root)
    accepted = L._validate(ledger, scope, now)
    L._row_bindings(list(rows.values()), accepted, scope, ledger)
    if (root / RECEIPT).exists():
        receipt = json.loads((root / RECEIPT).read_bytes())
        if (not isinstance(receipt, dict) or receipt.get('status') != 'APPLIED'
                or receipt.get('approval_sha256') != APPROVAL_SHA256
                or any(rows.get(k) != c['after'] for k, c in cases.items())
                or KEYS & {e['master_key'] for e in ledger['entries']}):
            raise ValueError('previous application is incomplete or changed; fresh review required')
        L._display(root, list(rows.values()))
        return dict(receipt, status='ALREADY_APPLIED')

    entries = {entry['master_key']: entry for entry in ledger['entries']}
    for key, case in cases.items():
        if (rows.get(key) != case['before'] or entries.get(key) != case['withdrawn_acceptance']
                or V._hash(case['after']) != case['after_sha256']
                or case['after']['status'] != '無効' or case['after']['sources']
                or case['after']['categories']):
            raise ValueError(f'approved before row or acceptance changed: {key}')
    candidate = dict(rows)
    for key, case in cases.items():
        candidate[key] = copy.deepcopy(case['after'])
    new_ledger = copy.deepcopy(ledger)
    new_ledger['entries'] = [e for e in ledger['entries'] if e['master_key'] not in KEYS]
    remaining = L._validate(new_ledger, scope, now)
    L._row_bindings(list(candidate.values()), remaining, scope, new_ledger)
    events = [['財務省', '掲載終了', c['display_name'], c['before']['remark'], c['after']['remark']]
              for c in approval['cases']]
    stamp = now.astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')

    with tempfile.TemporaryDirectory(prefix='approved-legacy-delist-') as folder:
        stage = Path(folder)
        for rel in ('data/dashboard/changes.csv', 'data/review/ofac_party_removal_queue.csv'):
            if (root / rel).exists():
                (stage / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / rel, stage / rel)
        M.save(candidate, stage / 'data/master/master.csv')
        _json(stage / L.LEDGER_PATH, new_ledger)
        D.write_list(stage, candidate)
        D.write_screening_gzip(stage, candidate)
        if V._read_rows((stage / 'data/master/master.csv').read_text(encoding='utf-8')) != candidate:
            raise ValueError('master projection changed an unapproved row')
        L._display(stage, list(candidate.values()))
        D.append_changes(stage, events, when=stamp)
        with (root / 'data/diff/latest.csv').open(encoding='utf-8', newline='') as stream:
            reader = csv.reader(stream, strict=True)
            if next(reader, []) != DIFF_COLS:
                raise ValueError('latest diff schema changed')
            previous_diff = list(reader)
        if any(len(row) != len(DIFF_COLS) for row in previous_diff):
            raise ValueError('latest diff contains a malformed row')
        diff_path = stage / 'data/diff/latest.csv'
        diff_path.parent.mkdir(parents=True, exist_ok=True)
        with diff_path.open('w', encoding='utf-8', newline='') as stream:
            csv.writer(stream).writerows([DIFF_COLS, *events,
                *[r for r in previous_diff if r not in events]])
        pending = [r for r in V.read_csv(root) if r[4] not in KEYS]
        with (stage / V.OUT).open('w', encoding='utf-8', newline='') as stream:
            csv.writer(stream, lineterminator='\n').writerows([V.COLS, *pending])
        receipt = dict(schema_version=1, status='APPLIED', count=6,
            approval_sha256=APPROVAL_SHA256, approved_at=approval['approved_at'],
            applied_at=now.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), run_url=run_url,
            master_before_sha256=hashlib.sha256((root / 'data/master/master.csv').read_bytes()).hexdigest(),
            master_after_sha256=hashlib.sha256((stage / 'data/master/master.csv').read_bytes()).hexdigest(),
            remaining_legacy_count=len(new_ledger['entries']),
            outputs={rel: hashlib.sha256((stage / rel).read_bytes()).hexdigest() for rel in TARGETS})
        _json(stage / RECEIPT, receipt)
        writes = [FileWrite(root / rel, lambda temp, rel=rel: shutil.copyfile(stage / rel, temp))
                  for rel in (*TARGETS, RECEIPT.as_posix())]
        atomic_replace_many(writes)
    return receipt


def main(argv=None) -> int:
    import os
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', required=True, action='store_true')
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args(argv)
    repo, run = os.getenv('GITHUB_REPOSITORY', ''), os.getenv('GITHUB_RUN_ID', '')
    result = apply(args.root, run_url=f'https://github.com/{repo}/actions/runs/{run}' if repo and run else '')
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
