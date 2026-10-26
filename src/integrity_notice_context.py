"""Offline text contexts, not name roles, Party IDs, listing status or approval.

This research companion does not participate in the publication gate. Historical
notice text may mention an alias, a linked target or an unrelated party. Preserve
the whole record and literal position so a reviewer can inspect that distinction.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from .integrity_readers import clean, decode

BASELINE = 'docs/evidence/source-integrity-20261024-person-triage.json.gz'
BASELINE_SHA256 = '737760c156f89a7f6275785d5c20d2596368590c012d59332163e0e221381c69'
TARGET_CLASSES = {'ORIGIN_UNRESOLVED', 'NOTICE_MENTION_ONLY', 'NON_NAME_FIELD_ONLY', 'WEAK_EVIDENCE_CONFLICT', 'NOTICE_ALIAS_FIELD_REVIEW'}


def sha(body):
    return hashlib.sha256(body).hexdigest()


class _Contexts(HTMLParser):
    """Preserve paragraph/double-break/table-row boundaries within one body."""
    allowed = {'div', 'p', 'a', 'font', 'strong', 'em', 'b', 'i', 'span', 'sup', 'sub',
               'br', 'hr', 'h2', 'h3', 'h4', 'ul', 'ol', 'li', 'table', 'thead', 'tbody',
               'tr', 'td', 'th', 'style'}
    void = {'br', 'hr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found = False
        self.stack = []
        self.text = []
        self.records = []
        self.section = self.heading = ''
        self.header = None
        self.header_text = []
        self.breaks = 0
        self.row = False
        self.cells = 0

    def flush(self):
        text = clean(''.join(self.text))
        self.text = []
        self.breaks = 0
        if text:
            self.records.append(dict(section=self.section, heading=self.heading, record=text,
                                     record_number=len(self.records) + 1,
                                     record_format='table_row' if self.row else 'text_block'))

    def handle_starttag(self, tag, attrs):
        if tag == 'div' and 'field--name-field-body' in dict(attrs).get('class', '').split():
            if self.found:
                raise ValueError('duplicate Recent Actions body')
            self.found = True
            self.stack = ['div']
            return
        if not self.stack:
            return
        if tag not in self.allowed:
            raise ValueError('unknown notice body element: ' + tag)
        parent = self.stack[-1]
        parents = {'div': {'div', 'td', 'th', 'li'}, 'p': {'div', 'td', 'th', 'li'},
                   'ul': {'div', 'td', 'th', 'li'}, 'ol': {'div', 'td', 'th', 'li'},
                   'li': {'ul', 'ol'}, 'table': {'div'}, 'thead': {'table'},
                   'tbody': {'table'}, 'tr': {'table', 'thead', 'tbody'},
                   'td': {'tr'}, 'th': {'tr'}, 'h2': {'div'}, 'h3': {'div'},
                   'h4': {'div'}, 'style': {'div'}}
        if ((tag in parents and parent not in parents[tag])
                or (parent in {'table', 'thead', 'tbody', 'tr', 'ul', 'ol'}
                    and tag not in {'thead', 'tbody', 'tr', 'td', 'th', 'li'})
                or (self.row and tag in {'table', 'tr'})):
            raise ValueError('unsupported notice element parent: ' + parent + '/' + tag)
        if tag in {'h2', 'h3', 'h4'}:
            if self.header:
                raise ValueError('nested notice heading')
            self.flush()
            self.header, self.header_text = tag, []
        elif tag in {'p', 'li', 'tr', 'div'}:
            if self.row:
                self.text.append(' ')
            else:
                self.flush()
            if tag == 'tr':
                self.row, self.cells = True, 0
        elif tag in {'td', 'th'}:
            if self.cells:
                self.text.append(' | ')
            self.cells += 1
        elif tag == 'hr':
            if self.row:
                self.text.append(' ')
            else:
                self.flush()
        elif tag == 'br':
            if self.row:
                self.text.append(' ')
            else:
                self.breaks += 1
                if self.breaks >= 2:
                    self.flush()
                else:
                    self.text.append(' ')
        if tag not in self.void:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.void:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if not self.stack:
            return
        if tag in self.void:
            raise ValueError('unexpected closing void element: ' + tag)
        if self.stack[-1] != tag:
            raise ValueError('unbalanced notice body element: ' + tag)
        if tag == self.header:
            heading = clean(''.join(self.header_text))
            if not heading:
                raise ValueError('empty notice heading')
            if tag in {'h2', 'h3'}:
                self.section, self.heading = heading, ''
            else:
                self.heading = heading
            self.header = None
        elif tag in {'p', 'li', 'tr', 'div'}:
            if self.row and tag != 'tr':
                self.text.append(' ')
            else:
                self.flush()
            if tag == 'tr':
                self.row = False
        self.stack.pop()

    def handle_data(self, data):
        if not self.stack or 'style' in self.stack:
            return
        if self.stack[-1] in {'table', 'thead', 'tbody', 'tr', 'ul', 'ol'} and data.strip():
            raise ValueError('text outside notice cell/list item')
        if self.header:
            self.header_text.append(data)
        else:
            if data.strip():
                self.breaks = 0
            self.text.append(data)


def read_records(body: bytes) -> list[dict]:
    parser = _Contexts()
    parser.feed(decode(body))
    parser.close()
    if not parser.found or parser.stack or parser.header or not parser.records:
        raise ValueError('missing/unclosed Recent Actions body')
    return parser.records


def read_contexts(body: bytes, names: set[str]) -> dict:
    records = read_records(body)
    matches = []
    patterns = [(name, re.compile(r'(?<!\w)' + re.escape(name) + r'(?!\w)', re.I))
                for name in sorted(names) if name.strip()]
    for record in records:
        for name, pattern in patterns:
            for hit in pattern.finditer(record['record']):
                matches.append(dict(record, name=name, start=hit.start(), end=hit.end()))
    return dict(record_count=len(records), matches=matches,
                matched_names=sorted({hit['name'] for hit in matches}))


def _body(path):
    raw = path.read_bytes()
    return gzip.decompress(raw) if path.suffix == '.gz' else raw


def build_report(root: Path, baseline_path: Path, *, baseline_sha256: str = BASELINE_SHA256) -> dict:
    root, baseline_path = root.resolve(), baseline_path.resolve()
    baseline_body = _body(baseline_path)
    if sha(baseline_body) != baseline_sha256:
        raise ValueError('baseline body hash mismatch')
    baseline = json.loads(baseline_body)
    if not isinstance(baseline, dict) or not isinstance(baseline.get('items'), list):
        raise ValueError('baseline structure unknown')
    targets = []
    identities = set()
    for item in baseline['items']:
        if (not isinstance(item, dict) or not isinstance(item.get('display_name'), str)
                or not item['display_name'].strip() or not re.fullmatch('[a-f0-9]{64}', item.get('review_id', ''))
                or not isinstance(item.get('classification'), str)):
            raise ValueError('baseline item invalid')
        if item['review_id'] in identities:
            raise ValueError('duplicate baseline review ID')
        identities.add(item['review_id'])
        if item['classification'] in TARGET_CLASSES:
            targets.append(dict(review_id=item['review_id'], display_name=item['display_name'],
                                baseline_classification=item['classification'], contexts=[]))
    directory = (root / 'docs/evidence/integrity-notices').resolve()
    if not directory.is_relative_to(root):
        raise ValueError('notice directory outside repository')
    manifest_path = directory / 'manifest.json'
    if not manifest_path.resolve().is_relative_to(directory):
        raise ValueError('notice manifest outside evidence area')
    manifest_body = manifest_path.read_bytes()
    manifest = json.loads(manifest_body)
    if not isinstance(manifest, list) or not manifest:
        raise ValueError('notice manifest structure unknown')
    documents, seen = [], set()
    names = {item['display_name'] for item in targets}
    byname = {}
    for item in targets:
        byname.setdefault(item['display_name'], []).append(item)
    for doc in manifest:
        if not isinstance(doc, dict):
            raise ValueError('notice manifest item unknown')
        rel, source_hash, url = (doc.get(k) for k in ('raw_path', 'source_hash', 'source_url'))
        if not all(isinstance(v, str) for v in (rel, source_hash, url)):
            raise ValueError('notice provenance missing')
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.netloc != 'ofac.treasury.gov'
                or not re.fullmatch('/recent-actions/[0-9]{8}', parsed.path) or parsed.query or parsed.fragment):
            raise ValueError('not an official Recent Actions URL')
        if (Path(rel).is_absolute() or '..' in Path(rel).parts
                or not (root / rel).resolve().is_relative_to(directory) or rel in seen):
            raise ValueError('unsafe/duplicate notice path')
        if not re.fullmatch('[a-f0-9]{64}', source_hash):
            raise ValueError('notice hash invalid')
        seen.add(rel)
        body = _body(root / rel)
        if sha(body) != source_hash:
            raise ValueError('notice body hash mismatch')
        contexts = read_contexts(body, names)
        proof = dict(raw_path=rel, source_url=url, source_hash=source_hash, current=False)
        documents.append(dict(proof, record_count=contexts['record_count'], matched_name_count=len(contexts['matched_names'])))
        for hit in contexts['matches']:
            for item in byname[hit['name']]:
                item['contexts'].append(dict(proof, **hit))
    return dict(schema_version=1, status='RESEARCH_ONLY', identity_verified=False,
                current_listing_verified=False, retention_approved=False,
                baseline_body_sha256=sha(baseline_body), manifest_sha256=sha(manifest_body),
                documents=documents, target_count=len(targets),
                matched_item_count=sum(bool(item['contexts']) for item in targets), items=targets,
                limitations=['Literal notice context does not determine name/alias/linked-target role or Party ID.',
                             'Headings describe historical notice sections, not current listing or a delisting instruction.',
                             'This report cannot approve retention or release the publication gate.'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--baseline-sha256', default=BASELINE_SHA256)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_report(args.root, args.baseline or args.root / BASELINE,
                              baseline_sha256=args.baseline_sha256)
        payload = (json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()
        with args.out.open('xb') as stream:
            stream.write(gzip.compress(payload, mtime=0))
    except (OSError, ValueError, EOFError) as exc:
        print('NOTICE CONTEXT FAILED: ' + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(dict(status=report['status'], target_count=report['target_count'],
                          matched_item_count=report['matched_item_count'], report=str(args.out))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
