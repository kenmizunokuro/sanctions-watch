"""UN official consolidated XML and committee press index readers.

The permanent reference is the party key. Names never identify a delisting.
Keep all XML fields in the fingerprint, including identification amendments.
"""
from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from urllib.parse import urljoin, urlsplit

from .mofa_sources import Tree

CATALOG_URL = 'https://main.un.org/securitycouncil/en/content/un-sc-consolidated-list'
XML_URL = 'https://scsanctions.un.org/resources/xml/en/name/consolidated.xml'
VESSELS_URL = 'https://main.un.org/securitycouncil/en/sanctions/1718/materials'
COMMITTEES = {
    'QD': '1267', 'IQ': '1518', 'CD': '1533', 'SD': '1591', 'KP': '1718',
    'IR': '1737', 'LY': '1970', 'TA': '1988', 'GB': '2048', 'YE': '2140',
    'SS': '2206', 'HT': '2653', 'SO': '2713', 'CF': '2745',
}
MAX_BYTES = 32 * 1024 * 1024


class UNSchemaError(ValueError):
    """Unknown, incomplete or unsafe official source structure."""


class UNTransportError(RuntimeError):
    """The official host did not deliver a complete document.

    Unlike a parse/schema change, HTTP 202 with an empty body is a
    retrieval failure and must remain visible as such in the source ledger.
    """


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def press_url(committee: str) -> str:
    if committee not in COMMITTEES.values():
        raise UNSchemaError('unknown UN committee: ' + committee)
    return f'https://main.un.org/securitycouncil/en/sanctions/{committee}/press-releases'


def validate_un_url(url: str) -> str:
    p = urlsplit(url)
    hosts = {'main.un.org', 'www.un.org', 'un.org', 'scsanctions.un.org', 'press.un.org',
             'docs.un.org', 'unsolprodfiles.blob.core.windows.net'}
    if (p.scheme != 'https' or p.hostname not in hosts or p.username or p.password
            or ':' in p.netloc or p.fragment or any(c.isspace() for c in url)):
        raise ValueError('UN URL forbidden')
    if p.hostname == 'unsolprodfiles.blob.core.windows.net' and not p.path.startswith('/publiclegacyxmlfiles/EN/'):
        raise ValueError('UN distribution path forbidden')
    return url


def _text(node, path: str) -> str:
    return ' '.join((node.findtext(path) or '').split())


def _canonical(node):
    return [node.tag, sorted(node.attrib.items()), ' '.join((node.text or '').split()),
            [_canonical(child) for child in node]]


def parse_xml(body: bytes) -> dict:
    try:
        text = body.decode('utf-8-sig')
        if len(body) > MAX_BYTES or re.search(r'<!\s*(?:DOCTYPE|ENTITY)', text, re.I):
            raise ValueError('unsafe XML declaration or size')
        root = ET.fromstring(text)
        generated = root.attrib['dateGenerated']
        if datetime.fromisoformat(generated.replace('Z', '+00:00')).tzinfo is None:
            raise ValueError('dateGenerated timezone missing')
        if root.tag != 'CONSOLIDATED_LIST' or sorted(n.tag for n in root) != ['ENTITIES', 'INDIVIDUALS']:
            raise ValueError('consolidated list sections missing or unknown')
        parties, data_ids = {}, set()
        for section, tag, marker in [('INDIVIDUALS', 'INDIVIDUAL', 'i'), ('ENTITIES', 'ENTITY', 'e')]:
            container = root.find(section)
            if not len(container):
                raise ValueError('empty UN section: ' + section)
            for node in container:
                if node.tag != tag:
                    raise ValueError('unknown party element')
                ref, data_id = _text(node, 'REFERENCE_NUMBER'), _text(node, 'DATAID')
                if (not re.fullmatch(r'[A-Z]{2}' + marker + r'\.\d{3,}', ref)
                        or ref[:2] not in COMMITTEES or ref in parties
                        or not data_id.isdigit() or data_id in data_ids):
                    raise ValueError('invalid/duplicate party ID or unknown regime: ' + ref)
                data_ids.add(data_id)
                listed = _text(node, 'LISTED_ON')
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:Z|[+-]\d{2}:\d{2})?', listed):
                    raise ValueError('invalid XSD listing date')
                date.fromisoformat(listed[:10])
                if len(listed) > 10:
                    datetime.fromisoformat(listed[:10] + 'T00:00:00' + listed[10:].replace('Z', '+00:00'))
                primary = ' '.join(filter(None, (_text(node, key) for key in
                                                ['FIRST_NAME', 'SECOND_NAME', 'THIRD_NAME', 'FOURTH_NAME'])))
                if not primary or not _text(node, 'UN_LIST_TYPE'):
                    raise ValueError('party primary name or list type missing')
                names = [dict(name=primary, kind='primary', weak=False)]
                original = _text(node, 'NAME_ORIGINAL_SCRIPT')
                if original:
                    names.append(dict(name=original, kind='original', weak=False))
                for alias in node.findall(tag + '_ALIAS'):
                    name, quality = _text(alias, 'ALIAS_NAME'), _text(alias, 'QUALITY')
                    if not name:
                        if quality:
                            raise ValueError('alias quality without name')
                        continue
                    allowed = {'Good', 'Low'} if marker == 'i' else {'a.k.a.', 'f.k.a.'}
                    if quality not in allowed:
                        raise ValueError('unknown alias quality: ' + quality)
                    names.append(dict(name=name, kind='former' if quality == 'f.k.a.' else 'alias',
                                      weak=quality == 'Low'))
                parties[ref] = dict(reference=ref, data_id=data_id, kind=tag.lower(),
                                    committee=COMMITTEES[ref[:2]], list_type=_text(node, 'UN_LIST_TYPE'),
                                    listed_on=listed, names=names, fingerprint=digest(_canonical(node)),
                                    evidence=_canonical(node))
        return dict(schema_version=1, generated_at=generated, parties=parties)
    except (ValueError, KeyError, UnicodeError, ET.ParseError) as error:
        raise UNSchemaError(str(error)) from error


def parse_notices(body: bytes, url: str) -> list[dict]:
    """Read dated press records from the actual Drupal press view, not navigation."""
    validate_un_url(url)
    tree = Tree(body).root
    view = next((n for n in tree.walk() if 'view-press-releases' in
                 n.attrs.get('class', '').split()), None)
    if view is None:
        raise UNSchemaError('UN press view missing')
    notices = {}
    for row in view.walk():
        if row.tag != 'tr' or not any(n.tag == 'td' for n in row.walk()):
            continue
        anchors = [n for n in row.walk() if n.tag == 'a']
        # Actual UN records include old www.un.org/press links, extensionless
        # links, missing language segments and empty external hrefs.
        links = [n for n in anchors if re.search(
            r'/(?:press/)?(?:(?:en|fr|ar|zh|ru|es)/)?\d{4}/sc\d+(?:\.doc\.htm)?(?:$|\?)',
            n.attrs.get('href', ''), re.I)]
        times = [n.attrs.get('datetime', '') for n in row.walk() if n.tag == 'time']
        if len(times) != 1 or len(links) > 1:
            raise UNSchemaError('unknown populated UN press row')
        link = links[0] if links else (anchors[-1] if anchors else None)
        if link is None:
            raise UNSchemaError('press title link missing')
        raw_url = link.attrs.get('href', '')
        if not raw_url or raw_url.startswith('http://www.un.org/press/'):
            # Use the explicitly supplied internal content link. Never invent a URL.
            internal = [n for n in anchors if re.fullmatch(
                r'/securitycouncil/en/(?:content/)?sc/?\d+(?:-\d+)?', n.attrs.get('href', ''), re.I)]
            if len(internal) != 1:
                raise UNSchemaError('legacy UN notice has no official content link')
            target = urljoin(url, internal[0].attrs['href'])
        else:
            target = raw_url
        try:
            validate_un_url(target)
        except ValueError as error:
            raise UNSchemaError('press notice URL forbidden') from error
        if urlsplit(target).hostname not in {'press.un.org', 'www.un.org', 'un.org', 'main.un.org'}:
            raise UNSchemaError('press notice not on official content host')
        try:
            published = datetime.fromisoformat(times[0].replace('Z', '+00:00'))
            if published.tzinfo is None:
                raise ValueError('missing timezone')
        except ValueError as error:
            raise UNSchemaError('invalid press date') from error
        notice = dict(url=target, title=' '.join(link.text().split()), publication_at=times[0])
        if not notice['title']:
            raise UNSchemaError('empty press title')
        if target in notices:
            previous = notices[target]
            if previous['publication_at'] != notice['publication_at']:
                raise UNSchemaError('conflicting duplicate press date')
            notice['title'] = ' / '.join(sorted(set(previous['title'].split(' / ') + [notice['title']])))
        notices[target] = notice
    if not notices:
        raise UNSchemaError('empty UN press index requires manual verification')
    return [notices[key] for key in sorted(notices)]
