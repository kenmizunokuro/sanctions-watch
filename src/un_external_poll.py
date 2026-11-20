"""Run the UN poll from an independent network and publish its freshness proof."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import un_press_rss as P, un_sources as U, un_watch as W
from .persistence import FileWrite, atomic_replace_many
from .un_lock import delegated_lock

RECEIPT = 'data/un/external_observation.json'
MAX_AGE = timedelta(minutes=35)


def _complete(state):
    checked = state.get('last_checked_at')
    if not checked or any(stamp != checked for stamp in (
        state.get('xml_last_success_at'),
        state.get('press_last_success_at'),
        state.get('press_rss', {}).get('last_success_at'),
    )):
        return False
    if any('press:' + committee not in state['documents'] for committee in U.COMMITTEES.values()):
        return False
    failures = state.get('failures', [])
    if any(issue['source'] in {'un_xml', 'un_press_rss'} or issue['source'].startswith('press:')
           for issue in failures):
        return False
    updates_failures = [issue for issue in failures if issue['source'] == 'un_updates']
    if not updates_failures:
        return state.get('updates_last_success_at') == checked
    # A valid official update table can still contain a reference conflict.
    return bool(state.get('updates_active_conflicts')) and all(
        issue['error_type'] == 'UNReferenceConflict' and issue['http_status'] in {200, 304}
        for issue in updates_failures
    )


def collect(root: Path) -> bool:
    with delegated_lock(root):
        result = W.run(root)
        state = W.load(root)
        complete = _complete(state)
        print(json.dumps(dict(result, external_sources_complete=complete), ensure_ascii=False))
        if not complete:
            return False
        receipt = dict(schema_version=1, checked_at=state['last_checked_at'], passed=result['ok'],
                       state_sha256=hashlib.sha256((root / W.STATE_PATH).read_bytes()).hexdigest())
        atomic_replace_many([FileWrite(root / RECEIPT, lambda path: W._write_json(path, receipt))])
        return True


def _read_published(root, now=None):
    with (root / RECEIPT).open('rb') as stream:
        receipt = json.loads(stream.read(4097))
    if receipt['schema_version'] != 1 or type(receipt['passed']) is not bool:
        raise ValueError('Invalid UN external observation receipt')
    stamp = P.moment(receipt['checked_at'])
    if now is not None and not timedelta(0) <= now - stamp < MAX_AGE:
        raise ValueError('UN external observation is future dated or stale')
    state_body = (root / W.STATE_PATH).read_bytes()
    if hashlib.sha256(state_body).hexdigest() != receipt['state_sha256']:
        raise ValueError('UN external observation receipt does not match state')
    state = json.loads(state_body)
    if state.get('last_checked_at') != receipt['checked_at'] or not _complete(state):
        raise ValueError('UN external observation is incomplete')
    return receipt, state


def recent(root: Path, *, now=None) -> bool:
    try:
        _read_published(root, now if now is not None else datetime.now(timezone.utc))
        return True
    except (OSError, ValueError, AttributeError, TypeError, KeyError):
        return False


def notify_published(root: Path, previous: str):
    from . import un_watch_notify as N

    if not re.fullmatch(r'[0-9a-f]{40}', previous):
        raise ValueError('An exact previous commit SHA is required')
    receipt, state = _read_published(root)
    before = json.loads(subprocess.run(['git', 'show', previous + ':' + W.STATE_PATH],
                                      cwd=root, capture_output=True, check=True).stdout)
    N.send(state, W.change_counts(before, state), failed=not receipt['passed'])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('collect', 'recent', 'notify'))
    parser.add_argument('--previous')
    parser.add_argument('--root', type=Path, default=Path.cwd())
    args = parser.parse_args()
    if args.action == 'collect':
        return 0 if collect(args.root) else 1
    if args.action == 'recent':
        return 0 if recent(args.root) else 1
    if not args.previous:
        parser.error('--previous is required for notify')
    notify_published(args.root, args.previous)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
