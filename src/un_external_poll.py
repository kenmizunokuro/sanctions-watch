"""Run the UN poll from an independent network and publish its freshness proof."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import un_press_rss as P, un_sources as U, un_watch as W
from .persistence import FileWrite, atomic_replace_many

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
    if state.get('updates_last_success_at') == checked:
        return True
    # A valid official update table can still contain a reference conflict.
    return bool(state.get('updates_active_conflicts')) and any(
        issue['source'] == 'un_updates' and issue['error_type'] == 'UNReferenceConflict'
        and issue['http_status'] in {200, 304} for issue in state.get('failures', [])
    )


def collect(root: Path) -> bool:
    result = W.run(root)
    state = W.load(root)
    complete = _complete(state)
    print(json.dumps(dict(result, external_sources_complete=complete), ensure_ascii=False))
    if not complete:
        return False
    receipt = dict(schema_version=1, checked_at=state['last_checked_at'],
                   state_sha256=hashlib.sha256((root / W.STATE_PATH).read_bytes()).hexdigest())
    atomic_replace_many([FileWrite(root / RECEIPT, lambda path: W._write_json(path, receipt))])
    return True


def recent(root: Path, *, now=None) -> bool:
    now = now if now is not None else datetime.now(timezone.utc)
    try:
        path = root / RECEIPT
        if not path.exists():
            return False
        with path.open('rb') as stream:
            receipt = json.loads(stream.read(4097))
        if receipt['schema_version'] != 1:
            return False
        stamp = P.moment(receipt['checked_at'])
        if not timedelta(0) <= now - stamp < MAX_AGE:
            return False
        state_body = (root / W.STATE_PATH).read_bytes()
        if hashlib.sha256(state_body).hexdigest() != receipt['state_sha256']:
            return False
        state = json.loads(state_body)
        return (state.get('last_checked_at') == receipt['checked_at'] and _complete(state))
    except (OSError, ValueError, AttributeError, TypeError, KeyError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('collect', 'recent'))
    parser.add_argument('--root', type=Path, default=Path.cwd())
    args = parser.parse_args()
    if args.action == 'collect':
        return 0 if collect(args.root) else 1
    return 0 if recent(args.root) else 1


if __name__ == '__main__':
    raise SystemExit(main())
