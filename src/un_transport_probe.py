"""Read-only evidence for UN HTTP availability; never sanctions validation.

An observation can report a successful HTTP200 without establishing that the
document is current, complete, or suitable for screening. Access challenges are
recorded, never solved or retried by this diagnostic.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import source_audit as A, un_sources as U, un_updates as V
from .fetch import NetworkPolicyError, fetch

RSS_URL = 'https://main.un.org/securitycouncil/en/feed/1.0/updates_unsc_consolidated_list'
TARGETS = (('xml', U.XML_URL), ('updates', V.UPDATES_URL),
           ('press_1267', U.press_url('1267')), ('rss', RSS_URL))
PUBLIC_HEADERS = frozenset({'server', 'content-type', 'content-length',
                            'cache-control', 'retry-after', 'x-amzn-waf-action',
                            'x-cache', 'via', 'etag', 'last-modified'})


def _headers(headers):
    return {str(k).lower(): str(v).replace('\x00', '')[:400]
            for k, v in headers.items() if str(k).lower() in PUBLIC_HEADERS}


def _transport_status(code, body, headers):
    if headers.get('x-amzn-waf-action', '').strip().lower() in {'challenge', 'captcha'}:
        return 'ACCESS_CHALLENGE'
    if code == 202 and not body:
        return 'EMPTY_202'
    if code == 200:
        return 'HTTP200_UNVALIDATED' if body else 'EMPTY_200'
    if code == 304:
        return 'UNEXPECTED_304'
    if code is not None and code >= 400:
        return 'HTTP_ERROR'
    return 'NON_DOCUMENT_STATUS' if code is not None else 'FETCH_ERROR'


def _rss_shape(body):
    # Only structural evidence, not target names, dates of detection, or actions.
    if len(body) > 1024 * 1024:
        return {'format': 'oversized_xml'}
    if re.search(br'<!\s*(?:DOCTYPE|ENTITY)', body, re.I):
        return {'format': 'unsafe_xml'}
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return {'format': 'invalid_xml'}
    channels = root.findall('channel')
    if root.tag != 'rss' or len(channels) != 1:
        return {'format': 'other_xml'}
    items = channels[0].findall('item')
    return {'format': 'rss', 'item_count': len(items),
            'distinct_guids': len({i.findtext('guid').strip() for i in items
                                   if (i.findtext('guid') or '').strip()}),
            'distinct_publication_labels': len({i.findtext('pubDate').strip() for i in items
                                                if (i.findtext('pubDate') or '').strip()})}


def probe(*, fetcher=fetch, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('probe timestamp must include timezone')
    result = {'schema_version': 1, 'checked_at': now.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
              'probe_completed': False, 'observations': []}
    for source, url in TARGETS:
        item = {'source': source, 'url': url, 'final_url': '', 'http_status': None,
                'body_bytes': None, 'sha256': None, 'etag': '', 'last_modified': '', 'headers': {}}
        body = None
        try:
            f = fetcher(url, allow_conditional=False, url_validator=U.validate_un_url,
                        max_bytes=U.MAX_BYTES, timeout=15)
            body = f.body
            item.update(final_url=A.safe_audit_url(f.final_url or url), http_status=int(f.http_status),
                        headers=_headers(f.headers), etag=f.etag, last_modified=f.last_modified)
            if body is not None:
                item.update(body_bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
        except (requests.RequestException, NetworkPolicyError, ValueError, OSError) as error:
            response = getattr(error, 'response', None)
            if response is not None:
                item.update(http_status=response.status_code,
                            final_url=A.safe_audit_url(response.url or url), headers=_headers(response.headers))
            item.update(error_type=type(error).__name__, error_message=A._clean_error(error))
        item['etag'] = item['headers'].get('etag', item['etag'])
        item['last_modified'] = item['headers'].get('last-modified', item['last_modified'])
        item['transport_status'] = _transport_status(item['http_status'], body, item['headers'])
        if source == 'rss' and item['transport_status'] == 'HTTP200_UNVALIDATED':
            item['rss_shape'] = _rss_shape(body)
        result['observations'].append(item)
    result['probe_completed'] = True
    return result


def main(argv=None, *, fetcher=fetch):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path('dist/un_transport_probe.json'))
    args = parser.parse_args(argv)
    report = probe(fetcher=fetcher)
    text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding='utf-8')
    print(text, end='')
    # Completion of evidence collection is distinct from official-source health.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
