"""Bounded official UN news sensor; titles are candidates, never legal actions."""
from __future__ import annotations

import copy
import csv
import gzip
import hashlib
import json
import re
import xml.etree.ElementTree as ET
import zlib
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from . import source_audit as A, un_sources as U

URL = 'https://press.un.org/en/rss.xml'
MAX_BYTES = 1024 * 1024
MAX_POLL_GAP = timedelta(minutes=90)
MAX_CONTENT_AGE = timedelta(hours=72)
KEYWORDS = re.compile(r'\bsanctions?\b|\bassets?\s+freeze\b|\barms\s+embargo\b|\btravel\s+ban\b', re.I)
ITEM_URL = re.compile(r'https://press\.un\.org/en/(?:\d{4}/[a-z0-9_-]+\.doc\.htm|blog/sc[0-9]+)')
RECOVERY_PATH = 'data/un/rss_archived_candidate.json'
RECOVERY_FIELDS = ('source_received_at', 'official_reference_url', 'official_reference')


class _NoDTD(ET.TreeBuilder):
    def doctype(self, name, public_id, system_id):
        raise U.UNSchemaError('RSS DTD declarations are forbidden')


def stamp(when):
    if when.tzinfo is None:
        raise ValueError('RSS timestamp must include timezone')
    return when.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def moment(value):
    when = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if when.tzinfo is None:
        raise ValueError('RSS timestamp must include timezone')
    return when


def _field(node, name):
    values = node.findall(name)
    if len(values) != 1 or len(values[0]) or not (values[0].text or '').strip():
        raise U.UNSchemaError('RSS missing, duplicate or nontext field: ' + name)
    text = ' '.join(values[0].text.split())
    if len(text) > 2048:
        raise U.UNSchemaError('RSS field exceeds limit: ' + name)
    return text


def parse(body: bytes, *, now: datetime) -> list[dict]:
    stamp(now)
    try:
        if not body or len(body) > MAX_BYTES:
            raise ValueError('RSS is empty or oversized')
        root = ET.fromstring(body, parser=ET.XMLParser(target=_NoDTD()))
        channels = root.findall('channel')
        if root.tag != 'rss' or root.get('version') != '2.0' or len(channels) != 1:
            raise ValueError('RSS2 channel structure changed')
        channel = channels[0]
        if _field(channel, 'link') != 'https://press.un.org/en':
            raise ValueError('RSS publisher link changed')
        _field(channel, 'title')
        nodes = channel.findall('item')
        if not 1 <= len(nodes) <= 200:
            raise ValueError('RSS item count outside 1..200')
        urls, guids, result = set(), set(), []
        for node in nodes:
            title, url, guid = (_field(node, k) for k in ['title', 'link', 'guid'])
            U.validate_un_url(url)
            if not ITEM_URL.fullmatch(url):
                raise ValueError('RSS article URL changed or not official')
            if url in urls or guid in guids:
                raise ValueError('RSS duplicate article URL or GUID')
            urls.add(url); guids.add(guid)
            published = parsedate_to_datetime(_field(node, 'pubDate'))
            if published.tzinfo is None or published > now + timedelta(minutes=10):
                raise ValueError('RSS publication has no timezone or is future dated')
            result.append(dict(url=url, guid=guid, title=title, publication_at=stamp(published),
                               candidate=bool(re.fullmatch(r'.*/sc\d+\.doc\.htm', url)
                                              and KEYWORDS.search(title))))
        return sorted(result, key=lambda item: item['url'])
    except (ET.ParseError, ValueError, TypeError, OverflowError, LookupError) as error:
        raise U.UNSchemaError('Invalid official press RSS: ' + str(error)) from error


def refresh_status(sensor):
    sensor['status'] = ('error' if sensor.get('error') else 'stale' if sensor.get('stale')
                        else 'coverage_gap' if sensor.get('coverage_gap') else 'observed')


def read_original(root, source_hash, *, now):
    if not re.fullmatch('[0-9a-f]{64}', source_hash):
        raise ValueError('RSS source hash must be exact SHA256')
    archive = root / 'data/raw/un'
    raw = archive / (source_hash + '.xml.gz')
    if (not archive.resolve().is_relative_to(root.resolve())
            or not raw.resolve().is_relative_to(archive.resolve())):
        raise ValueError('RSS original lies outside the archive')
    try:
        with gzip.open(raw, 'rb') as stream:
            body = stream.read(MAX_BYTES + 1)
    except (OSError, EOFError, zlib.error) as error:
        raise ValueError('RSS original is unavailable or corrupted') from error
    if len(body) > MAX_BYTES or hashlib.sha256(body).hexdigest() != source_hash:
        raise ValueError('RSS original hash mismatch or archived response too large')
    return parse(body, now=now)


def _recovery_evidence(root, *, now):
    path = root / RECOVERY_PATH
    if not path.exists():
        return None
    request = json.loads(path.read_text(encoding='utf-8'))
    required = ('article_url', 'source_hash', *RECOVERY_FIELDS)
    if (not isinstance(request, dict) or request.get('schema_version') != 1
            or any(not isinstance(request.get(key), str) or not request[key] for key in required)):
        raise ValueError('RSS archive recovery request schema invalid')
    article = request['article_url']
    U.validate_un_url(article)
    U.validate_un_url(request['official_reference_url'])
    received = moment(request['source_received_at'])
    if stamp(received) != request['source_received_at'] or received > now:
        raise ValueError('RSS archive recovery receipt time invalid')
    source_hash = request['source_hash']
    items = read_original(root, source_hash, now=received)
    records = [item for item in items if item['url'] == article and item['candidate']]
    if len(records) != 1:
        raise ValueError('Requested article is not a unique sanctions candidate in the archived RSS')
    record = records[0]
    original_row = dict(checked_at=request['source_received_at'], source='un_press_rss',
                        document_role='auxiliary_global_news', status='error', http_status='200',
                        url=URL, final_url=URL, content_hash=source_hash,
                        raw_path=f'data/raw/un/{source_hash}.xml.gz', fetch_failed='0',
                        schema_changed='1', error_type='UNSchemaError')
    with (root / f'data/source_audit/{received:%Y-%m}.csv').open(encoding='utf-8', newline='') as stream:
        reader = csv.DictReader(stream, strict=True)
        if reader.fieldnames != A.AUDIT_COLS or sum(
                all(row.get(key) == value for key, value in original_row.items()) for row in reader) != 1:
            raise ValueError('RSS archive recovery has no unique audited original receipt')

    return request, record


def _validate_recovery_event(event, request, record):
    if (event['source_hash'] != request['source_hash']
            or event['record'] != record
            or event.get('event_id') != U.digest(['', record, request['source_hash']])
            or any(event.get(key) != request[key] for key in RECOVERY_FIELDS)
            or moment(event['detected_at']) < moment(request['source_received_at'])):
        raise ValueError('RSS archive recovery provenance differs from the audited original')


def verify_recovery_provenance(root, event, *, now):
    if not any(key in event for key in RECOVERY_FIELDS):
        return
    evidence = _recovery_evidence(root, now=now)
    if evidence is None:
        raise ValueError('RSS archive recovery provenance request missing')
    _validate_recovery_event(event, *evidence)


def recover_archived_candidate(root, sensor, *, now):
    evidence = _recovery_evidence(root, now=now)
    if evidence is None:
        return None
    request, record = evidence
    article = record['url']
    source_hash = request['source_hash']
    event_id = U.digest(['', record, source_hash])
    existing = [event for event in sensor.get('events', []) if event['record']['url'] == article]
    if existing:
        recovered = [event for event in existing if event['event_id'] == event_id]
        if len(recovered) != 1:
            raise ValueError('RSS archive recovery conflicts with existing article history')
        _validate_recovery_event(recovered[0], request, record)
        return None
    if article in sensor.get('heads', {}):
        raise ValueError('RSS archive recovery found an orphan article head')
    if not sensor.get('last_success_at'):
        raise ValueError('RSS archive recovery needs an initialized live sensor')
    if any(item['url'] == article and item != record for item in sensor.get('items', [])):
        raise ValueError('RSS archive recovery would attach an older article to a different live record')
    event = dict(event_id=event_id, parent_event_id='', record=record,
                 previous_record={}, previous_source_hash='', source_hash=source_hash,
                 detected_at=stamp(now), status='PENDING_REVIEW', change='NEW',
                 **{key: request[key] for key in RECOVERY_FIELDS})
    sensor.setdefault('events', []).append(event)
    sensor.setdefault('heads', {})[article] = event_id
    return event


def observe(previous: dict, items: list[dict], *, source_hash: str, now: datetime) -> dict:
    when = stamp(now)
    sensor = copy.deepcopy(previous)
    sensor.pop('error', None)
    sensor.setdefault('events', [])
    sensor.setdefault('heads', {})
    old_items = {x['url']: x for x in previous.get('items', [])}
    by_id = {e['event_id']: e for e in sensor['events']}
    initialized = bool(previous.get('last_success_at'))
    prior_at = moment(previous['last_success_at']) if initialized else None
    if prior_at and now < prior_at:
        raise ValueError('RSS observation time moved backwards')
    reasons = []
    if old_items and not set(old_items).intersection(x['url'] for x in items):
        reasons.append('no overlapping recent RSS items')
    if prior_at and now - prior_at > MAX_POLL_GAP:
        reasons.append('RSS polling interval exceeded 90 minutes')
    if reasons:
        sensor.setdefault('coverage_gap', dict(detected_at=when, reason='; '.join(reasons),
                                              source_hash=source_hash,
                                              previous_success_at=previous['last_success_at']))
    new_candidates = new_articles = revised_articles = 0
    for item in items:
        head = sensor['heads'].get(item['url'], '')
        old = old_items.get(item['url']) or by_id.get(head, {}).get('record')
        if old == item or not (item['candidate'] or (old or {}).get('candidate')):
            continue
        event_id = U.digest([head, item, source_hash])
        status = 'PENDING_REVIEW' if initialized else 'BASELINE_RECORDED'
        previous_hash = ''
        if old:
            previous_hash = (previous.get('source_hash', '') if item['url'] in old_items
                             else by_id.get(head, {}).get('source_hash', ''))
        sensor['events'].append(dict(event_id=event_id, parent_event_id=head, record=item,
                                     previous_record=old or {}, source_hash=source_hash,
                                     previous_source_hash=previous_hash,
                                     detected_at=when, status=status,
                                     change='REVISION' if old else 'NEW'))
        sensor['heads'][item['url']] = event_id
        new_candidates += status == 'PENDING_REVIEW'
        if status == 'PENDING_REVIEW':
            if old:
                revised_articles += 1
            else:
                new_articles += 1
    newest = max(moment(x['publication_at']) for x in items)
    sensor.update(items=items, last_success_at=when, latest_publication_at=stamp(newest),
                  source_hash=source_hash, new_candidates=new_candidates,
                  new_articles=new_articles, revised_articles=revised_articles,
                  stale=now - newest > MAX_CONTENT_AGE)
    refresh_status(sensor)
    return sensor
