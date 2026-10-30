"""Independent UN monitoring; validated snapshots never imply master application."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import dashboard, source_audit as A, state as S, un_sources as U
from .fetch import fetch, read_raw
from .persistence import FileWrite, atomic_replace_many
from .un_lock import locked

STATE_PATH = 'data/un/state.json'
SNAPSHOT_PATH = 'data/un/snapshot.json'
REVIEW_COLS = ['イベントID', '検知日時', '区分', '国連参照番号', '対象名／発表',
               '状態', '追加対象者', '掲載終了候補', '変更対象者', 'SHA256', '一次ソースURL']
NAME_COLS = ['reference', 'name', 'kind', 'weak', 'current', 'first_seen', 'last_seen',
             'list_type', 'listed_on', 'source_hash', 'source_url', 'raw_path']


def load(root: Path) -> dict:
    path = root / STATE_PATH
    if not path.exists():
        return dict(schema_version=1, documents={}, events=[], notices={}, coverage_gaps={})
    state = json.loads(path.read_text(encoding='utf-8'))
    if (state.get('schema_version') != 1 or not isinstance(state.get('documents'), dict)
            or not isinstance(state.get('events'), list) or not isinstance(state.get('notices'), dict)):
        raise U.UNSchemaError('UN state schema invalid')
    return state


def _write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')


def _name_history(root, snapshot, when):
    rows = {}
    path = root / 'data/un/name_history.csv'
    if path.exists():
        with path.open(encoding='utf-8', newline='') as file:
            reader = csv.DictReader(file)
            if reader.fieldnames != NAME_COLS:
                raise U.UNSchemaError('UN name history columns changed')
            for row in reader:
                key = (row['reference'], row['name'], row['kind'], row['weak'])
                if key in rows:
                    raise U.UNSchemaError('duplicate UN name history identity')
                rows[key] = dict(row, current='0')
    for ref, party in snapshot['parties'].items():
        for name in party['names']:
            weak = '1' if name['weak'] else '0'
            key = (ref, name['name'], name['kind'], weak)
            previous = rows.get(key, {})
            rows[key] = dict(reference=ref, name=name['name'], kind=name['kind'], weak=weak,
                             current='1', first_seen=previous.get('first_seen', when), last_seen=when,
                             list_type=party['list_type'], listed_on=party['listed_on'],
                             source_hash=snapshot['source_hash'], source_url=U.XML_URL,
                             raw_path=snapshot['raw_path'])
    return [rows[k] for k in sorted(rows)]


def _write_names(path, rows):
    with path.open('w', encoding='utf-8', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=NAME_COLS, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def _review_rows(state: dict) -> list[list]:
    rows = []
    for event in state['events']:
        if event['status'] not in {'PENDING_REVIEW', 'APPROVED'}:
            continue
        changes = event['application_diff']
        rows.append([event['event_id'], event['detected_at'],
                     '初回収録（国連）' if not event['application_base_event_id'] else '国連XML差分', '', '',
                     event['status'], sum(c['change'] == '追加' for c in changes),
                     sum(c['change'] == '掲載終了候補' for c in changes),
                     sum(c['change'] == '情報改訂' for c in changes),
                     event['source_hash'], U.CATALOG_URL])
        for change in changes:
            rows.append([event['event_id'] + ':' + change['reference'], event['detected_at'],
                         '初回収録（国連）' if not event['application_base_event_id'] else change['change'],
                         change['reference'], change['after_name'] or change['before_name'],
                         event['status'], '', '', '', event['source_hash'], U.CATALOG_URL])
    for notice in state['notices'].values():
        if notice['status'] == 'PENDING_REVIEW':
            rows.append([notice['event_id'], notice['detected_at'], '国連委員会発表', '', notice['title'],
                         notice['status'], '', '', '', notice['index_hash'], notice['url']])
    # Fetch failures must be visible in the operational review queue, not
    # just buried inside a large JSON ledger or a generic dashboard alert.
    # Stable keys preserve reviewer notes between repeated failed polls.
    for issue in state.get('failures', []):
        source = issue.get('source', '')
        if not source.startswith('press:'):
            continue
        committee = source.split(':', 1)[1]
        if committee not in U.COMMITTEES.values():
            raise U.UNSchemaError('unknown committee in fetch failure record')
        http = str(issue.get('http_status') or 'unknown')
        message = str(issue.get('message') or issue.get('error_type') or 'unknown')
        rows.append(['fetch:' + committee, state['last_checked_at'], '取得エラー・要確認',
                     '', committee + '委員会 HTTP ' + http + ': ' + message,
                     'FETCH_FAILED', '', '', '', '', U.press_url(committee)])
    for committee, gap in sorted(state['coverage_gaps'].items()):
        rows.append(['coverage:' + committee, gap['detected_at'], '未確認期間あり', '', committee,
                     'REVIEW_REQUIRED', '', '', '', '', U.press_url(committee)])
    return rows


def write_reviews(path: Path, state: dict):
    with path.open('w', encoding='utf-8', newline='') as file:
        writer = csv.writer(file, lineterminator='\n')
        writer.writerow(REVIEW_COLS)
        writer.writerows(_review_rows(state))


def projections(root: Path, state: dict, *, now: datetime) -> list[FileWrite]:
    # Project the in-memory UN bundle so status and review queue share its generation.
    rows = dashboard.build_status_rows(root, [], S.load_state(root), now=now, un_state=state)
    status_path = root / 'data/dashboard/status.csv'
    if status_path.exists():
        with status_path.open(encoding='utf-8', newline='') as file:
            reader = csv.reader(file)
            if next(reader, None) != dashboard.STATUS_COLS:
                raise U.UNSchemaError('existing dashboard status columns changed')
            saved = list(reader)
        known = set(dashboard.SOURCE_LABEL.values())
        if any(len(row) != len(dashboard.STATUS_COLS) or row[0] not in known for row in saved):
            raise U.UNSchemaError('invalid dashboard status row')
        saved_by_source = {r[0]: r for r in saved}
        if len(saved_by_source) != len(saved):
            raise U.UNSchemaError('duplicate dashboard source row')
        other = {label for key, label in dashboard.SOURCE_LABEL.items() if not key.startswith('un_')}
        if not other.issubset(saved_by_source):
            raise U.UNSchemaError('other source dashboard observation missing')
        rows = [saved_by_source[row[0]] if row[0] in other else row for row in rows]
    return [FileWrite(root / STATE_PATH, lambda p: _write_json(p, state)),
            FileWrite(root / 'data/dashboard/un_reviews.csv', lambda p: write_reviews(p, state)),
            FileWrite(root / 'data/dashboard/status.csv', lambda p: dashboard.write_status_rows(p, rows))]


def _document(root, key, url, previous, parser, fetcher):
    fetched = fetcher(url, prev=previous, url_validator=U.validate_un_url,
                      max_bytes=U.MAX_BYTES, timeout=30)
    try:
        return _read_document(root, key, url, previous, parser, fetched)
    except Exception as error:
        error.fetched = fetched
        raise


def _read_document(root, key, url, previous, parser, fetched):
    U.validate_un_url(fetched.final_url or url)
    if fetched.not_modified:
        path = previous.get('raw_path', '')
        if not path:
            raise U.UNSchemaError('304 without verified UN cache')
        raw = root / path
        if not raw.resolve().is_relative_to((root / 'data/raw/un').resolve()):
            raise U.UNSchemaError('UN cache path outside archive')
        fetched.raw_path = path
        body = read_raw(raw)
        if hashlib.sha256(body).hexdigest() != previous.get('sha256'):
            raise U.UNSchemaError('UN 304 raw hash mismatch')
    else:
        body = fetched.body
        if body is None:
            raise U.UNTransportError('UN response body missing: HTTP ' + str(fetched.http_status))
        actual = hashlib.sha256(body).hexdigest()
        if actual != fetched.sha256:
            raise U.UNSchemaError('UN response hash mismatch')
        suffix = 'xml' if key == 'xml' else 'html'
        raw = root / f'data/raw/un/{actual}.{suffix}.gz'
        raw.parent.mkdir(parents=True, exist_ok=True)
        if raw.exists():
            if hashlib.sha256(read_raw(raw)).hexdigest() != actual:
                raise U.UNSchemaError('existing UN content-addressed archive is corrupted')
        else:
            archived = gzip.compress(body, mtime=0)
            atomic_replace_many([FileWrite(raw, lambda p: p.write_bytes(archived))])
        fetched.raw_path = str(raw.relative_to(root))
    if not body:
        raise U.UNTransportError('empty UN response: HTTP ' + str(fetched.http_status))
    if fetched.http_status not in {200, 304}:
        raise U.UNTransportError('UN response not a complete 200/304: HTTP ' + str(fetched.http_status))
    # Even on 304, reparse verified original bytes. No stale parse cache shortcuts.
    parsed = parser(body)
    metadata = dict(url=url, final_url=A.safe_audit_url(fetched.final_url),
                    sha256=fetched.sha256, etag=fetched.etag, last_modified=fetched.last_modified,
                    raw_path=fetched.raw_path, filename=fetched.filename)
    return fetched, metadata, parsed


def _xml_event(root, state, snapshot, source_hash, now):
    previous = snapshot.get('previous_parties', {})
    parties = snapshot['parties']
    added = sorted(parties.keys() - previous.keys())
    removed = sorted(previous.keys() - parties.keys())
    changed = sorted(ref for ref in parties.keys() & previous.keys()
                     if parties[ref]['fingerprint'] != previous[ref]['fingerprint'])
    parent = state.get('current_event_id', '')
    event = dict(event_id=U.digest([parent, source_hash]), parent_event_id=parent,
                 source_hash=source_hash, detected_at=now, baseline=not bool(parent),
                 added=added, removed=removed, changed=changed, status='PENDING_REVIEW')
    applied_id = state.get('applied_event_id', '')
    base_parties = {}
    if applied_id:
        applied = next(e for e in state['events'] if e['event_id'] == applied_id)
        base = json.loads((root / f'data/un/snapshots/{applied["source_hash"]}.json').read_text(encoding='utf-8'))
        raw = read_raw(root / base['raw_path'])
        if hashlib.sha256(raw).hexdigest() != applied['source_hash'] or U.parse_xml(raw)['parties'] != base['parties']:
            raise U.UNSchemaError('applied UN baseline evidence is corrupted')
        base_parties = base['parties']
    application_diff = []
    for ref in sorted(parties.keys() | base_parties.keys()):
        before, after = base_parties.get(ref, {}), parties.get(ref, {})
        if before.get('fingerprint') == after.get('fingerprint'):
            continue
        application_diff.append(dict(reference=ref, change='追加' if not before else '掲載終了候補' if not after else '情報改訂',
                                     before_name=before.get('names', [{}])[0].get('name', ''),
                                     after_name=after.get('names', [{}])[0].get('name', ''),
                                     before_names=before.get('names', []), after_names=after.get('names', []),
                                     before_fingerprint=before.get('fingerprint', ''), after_fingerprint=after.get('fingerprint', '')))
    event.update(application_base_event_id=applied_id, application_diff=application_diff)
    for old in state['events']:
        if old['status'] in {'PENDING_REVIEW', 'APPROVED'}:
            old['status'] = 'SUPERSEDED'
    state['events'].append(event)
    state['current_event_id'] = event['event_id']
    return event


@locked
def run(root: Path, *, fetcher=fetch, now=None) -> dict:
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('now must include timezone')
    now = now.astimezone(timezone.utc)
    when = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    state = load(root)
    audit, writes, failures = [], [], []
    xml_ok = True
    try:
        previous_doc = state['documents'].get('xml', {})
        fetched, metadata, snapshot = _document(root, 'xml', U.XML_URL, previous_doc, U.parse_xml, fetcher)
        previous_snapshot = {}
        if state.get('snapshot_hash'):
            previous_snapshot = json.loads((root / SNAPSHOT_PATH).read_text(encoding='utf-8'))
            if U.digest(previous_snapshot) != state.get('snapshot_digest'):
                raise U.UNSchemaError('UN snapshot hash mismatch')
        # Truncation is not a mass delisting. Keep the previous snapshot for manual diagnosis.
        old_count = len(previous_snapshot.get('parties', {}))
        if old_count and len(snapshot['parties']) < old_count * .8:
            raise U.UNSchemaError('UN party count dropped more than 20 percent')
        snapshot.update(source_hash=metadata['sha256'], source_url=U.XML_URL,
                        raw_path=metadata['raw_path'])
        history = _name_history(root, snapshot, when)
        event = None
        if metadata['sha256'] != state.get('snapshot_hash'):
            snapshot['previous_parties'] = previous_snapshot.get('parties', {})
            event = _xml_event(root, state, snapshot, metadata['sha256'], when)
            snapshot.pop('previous_parties')
            snapshot.update(source_hash=metadata['sha256'], source_url=U.XML_URL,
                            raw_path=metadata['raw_path'])
            state['snapshot_hash'] = metadata['sha256']
            state['snapshot_digest'] = U.digest(snapshot)
            writes += [FileWrite(root / SNAPSHOT_PATH, lambda p, v=snapshot: _write_json(p, v)),
                       FileWrite(root / f'data/un/snapshots/{metadata["sha256"]}.json',
                                 lambda p, v=snapshot: _write_json(p, v))]
        state['documents']['xml'] = metadata
        writes.append(FileWrite(root / 'data/un/name_history.csv', lambda p: _write_names(p, history)))
        state.update(record_count=len(snapshot['parties']), source_updated=snapshot['generated_at'],
                     xml_last_success_at=when)
        audit.append(A.entry('un_xml', 'consolidated_xml', 'validated', fetched=fetched,
                             source_updated=snapshot['generated_at'], record_count=len(snapshot['parties']),
                             diff_counts={k: len(event[k]) if event else 0 for k in ['added', 'removed', 'changed']}))
    except Exception as error:
        xml_ok = False
        failures.append(dict(source='un_xml', error_type=type(error).__name__, message=A._clean_error(error),
                             http_status=getattr(getattr(error, 'fetched', None), 'http_status', '')))
        audit.append(A.error_entry('un_xml', 'consolidated_xml', error, url=U.XML_URL,
                                   schema_changed=isinstance(error, U.UNSchemaError),
                                   fetch_failed=not isinstance(error, U.UNSchemaError)))

    press_ok = True
    for committee in sorted(set(U.COMMITTEES.values())):
        key, url = 'press:' + committee, U.press_url(committee)
        try:
            prev = state['documents'].get(key, {})
            fetched, metadata, notices = _document(root, key, url, prev,
                                                   lambda body: U.parse_notices(body, url), fetcher)
            old_items = {n['url']: n for n in prev.get('items', [])}
            if old_items and not (set(old_items) & {n['url'] for n in notices}):
                state['coverage_gaps'].setdefault(committee, dict(detected_at=when, reason='no overlapping press items'))
            for notice in notices:
                old = old_items.get(notice['url'])
                if old == notice:
                    continue
                head_key = committee + ':' + notice['url']
                head = state.setdefault('notice_heads', {}).get(head_key, '')
                event_id = U.digest([committee, notice, head, metadata['sha256']])
                state['notices'][event_id] = dict(notice, event_id=event_id, committee=committee,
                                                  detected_at=when, index_hash=metadata['sha256'],
                                                  status='PENDING_REVIEW' if prev else 'BASELINE_RECORDED')
                state['notice_heads'][head_key] = event_id
            metadata['items'] = notices
            state['documents'][key] = metadata
            audit.append(A.entry('un_press', 'committee_' + committee, 'validated', fetched=fetched,
                                 record_count=len(notices)))
        except Exception as error:
            press_ok = False
            failures.append(dict(source=key, error_type=type(error).__name__, message=A._clean_error(error),
                                 http_status=getattr(getattr(error, 'fetched', None), 'http_status', '')))
            audit.append(A.error_entry('un_press', 'committee_' + committee, error, url=url,
                                       schema_changed=isinstance(error, U.UNSchemaError),
                                       fetch_failed=not isinstance(error, U.UNSchemaError)))
    pending = any(e['status'] in {'PENDING_REVIEW', 'APPROVED'} for e in state['events'])
    state['xml_status'] = 'error' if not xml_ok else 'review_required' if pending else 'unchanged'
    state['press_status'] = ('error' if not press_ok or state['coverage_gaps'] else 'review_required'
                             if any(n['status'] == 'PENDING_REVIEW' for n in state['notices'].values()) else 'unchanged')
    state.update(last_checked_at=when, failures=failures)
    if press_ok:
        state['press_last_success_at'] = when
    hb = [dict(source=key, status=state[family + '_status'], content_hash=state.get('snapshot_hash', '') if family == 'xml' else '',
               source_updated=state.get('source_updated', '') if family == 'xml' else '',
               record_count=state.get('record_count', '') if family == 'xml' else len(state['notices']))
          for key, family in [('un_xml', 'xml'), ('un_press', 'press')]]
    writes += projections(root, state, now=now)
    writes += [FileWrite(root / f'data/source_audit/{now:%Y-%m}.csv',
                         lambda p: A.append_rows(p, audit, now=now), seed_existing=True),
               FileWrite(root / f'data/heartbeat/{now:%Y-%m}.csv',
                         lambda p: S.append_heartbeat(p, hb, now=now), seed_existing=True)]
    atomic_replace_many(writes)
    return dict(ok=xml_ok and press_ok and not state['coverage_gaps'],
                snapshot_hash=state.get('snapshot_hash', ''), failures=failures,
                review_count=len(_review_rows(state)), xml_status=state['xml_status'], press_status=state['press_status'])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    before = load(args.root)
    result = run(args.root)
    after = load(args.root)
    result['new_xml_events'] = len(after['events']) - len(before['events'])
    result['new_notices'] = sum(n['status'] == 'PENDING_REVIEW' and key not in before['notices']
                                for key, n in after['notices'].items())
    output = os.environ.get('GITHUB_OUTPUT')
    if output:
        with open(output, 'a', encoding='utf-8') as file:
            for key in ['new_xml_events', 'new_notices', 'review_count']:
                file.write(f'{key}={result[key]}\n')
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
