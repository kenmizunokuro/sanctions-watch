"""Bounded official UN news sensor; titles are candidates, never legal actions."""
from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from . import un_sources as U

URL = 'https://press.un.org/en/rss.xml'
MAX_BYTES = 1024 * 1024
MAX_POLL_GAP = timedelta(minutes=90)
MAX_CONTENT_AGE = timedelta(hours=72)
KEYWORDS = re.compile(r'\bsanctions?\b|\bassets?\s+freeze\b|\barms\s+embargo\b|\btravel\s+ban\b', re.I)


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
            if not re.fullmatch(r'https://press\.un\.org/en/\d{4}/[a-z0-9_-]+\.doc\.htm', url):
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
        sensor['events'].append(dict(event_id=event_id, parent_event_id=head, record=item,
                                     previous_record=old or {}, source_hash=source_hash,
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
