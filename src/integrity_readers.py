"""Independent, offline witnesses. Never import ingestion parsers or perform I/O.

Only name-key normalization is shared by the auditor, at the master boundary.
Unrecognized/ambiguous structures raise instead of becoming an empty snapshot.
"""
from __future__ import annotations

import csv
import io
import re
import xml.sax
from xml.sax.handler import ContentHandler, feature_namespaces


class ReadError(ValueError):
    pass


MOF_COLUMNS = [
    '区分', '番号', '告示日付', '告示番号', '個人・団体',
    '氏名（日本語）', '氏名（英語）', '別名・別称（日本語）', '別名・別称（英語）',
    '旧称（日本語）', '旧称（英語）', '確定に十分でない別名（日本語）', '確定に十分でない別名（英語）',
    '称号（日本語）', '称号（英語）', '役職（日本語）', '役職（英語）', '生年月日',
    '出生地（日本語）', '出生地（英語）', '国籍（日本語）', '国籍（英語）', '旅券番号', '身分証番号',
    '住所・所在地（国）（日本語）', '住所・所在地（都市その他の情報）（日本語）',
    '住所・所在地（国）（英語）', '住所・所在地（都市その他の情報）（英語）',
    '国連参照番号', 'リスト掲載日', 'その他の情報', '外務省告示情報',
]
MOF_NAME_COLUMNS = MOF_COLUMNS[5:13]
NULLS = {'', '-', '‐', '―', 'ー', 'なし', '不明', 'N/A', 'n/a'}
NAMESPACE = 'https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ADVANCED_XML'


def clean(value):
    return ' '.join(str(value).replace('\u200b', '').replace('\ufeff', '').split())


def decode(body):
    for encoding in ('utf-8-sig', 'cp932', 'shift_jis'):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ReadError('原本の文字コードを確定できない')


def _row(source, label, ident, name, primary, weak=False, **evidence):
    return dict(source=source, category=label, source_id=ident, name=clean(name),
                primary=primary, weak=weak, **evidence)


def read_classic(body: bytes, label: str, alias: bool = False) -> list[dict]:
    out, ids = [], set()
    try:
        for line, row in enumerate(csv.reader(io.StringIO(decode(body)), strict=True), 1):
            if not row or row == ['\x1a']:
                continue
            required = 5 if alias else 12
            if len(row) != required or not row[0].strip().isdigit():
                raise ReadError(f'Classic CSV構造不明: line={line} columns={len(row)}')
            ident = row[0].strip()
            if not alias and ident in ids:
                raise ReadError(f'Classic Party ID重複: {ident}')
            ids.add(ident)
            name = clean(row[3 if alias else 1])
            if name in {'', '-0-'}:
                raise ReadError(f'Classic名称不明: line={line}')
            # A compound classic cell needs explicit review; never use the
            # production split_aliases parser to manufacture an expected name.
            if re.search(r'a\.\s*k\.\s*a\.|別名|別称', name, re.I):
                raise ReadError(f'Classic複合名称の独立判定不能: line={line}: {name}')
            out.append(_row('OFAC', label, ident, name, not alias,
                            format='classic_csv', locator=f'line:{line}', raw_record=row))
    except csv.Error as exc:
        raise ReadError(f'Classic CSV解析不能: {exc}') from exc
    if not out:
        raise ReadError('Classic CSV名称0件')
    return out


def _boolean(attrs, field):
    value = attrs.get((None, field), 'false').strip().lower()
    if value not in {'true', 'false'}:
        raise ReadError(f'XML {field}値が不明: {value}')
    return value == 'true'


class _Witness(ContentHandler):
    def __init__(self, label):
        super().__init__()
        self.label = label
        self.path = []
        self.ids = set()
        self.out = []
        self.party = None
        self.identity_false = False
        self.alias = None
        self.document = None
        self.capture = None
        self.buffer = []
        self.parts = {}
        self.locator = None

    def setDocumentLocator(self, locator):
        self.locator = locator

    def startElementNS(self, name, qname, attrs):
        ns, tag = name
        if ns != NAMESPACE:
            raise ReadError(f'XML namespace変更: {ns}')
        if not self.path and (tag != 'Sanctions' or attrs.get((None,'Version')) != '3'):
            raise ReadError('XML root/version変更')
        parent=self.path[-1] if self.path else None
        allowed={'Identity':{'Alias','NamePartGroups'},
                 'Profile':{'Identity','Feature'},'DistinctParty':{'Profile','Comment'},
                 'Alias':{'DocumentedName'},'DocumentedName':{'DocumentedNamePart'},
                 'DocumentedNamePart':{'NamePartValue'},'NamePartValue':set()}
        if parent in allowed and tag not in allowed[parent]:
            raise ReadError(f'XML 名称構造変更: {parent}/{tag}')
        self.path.append(tag)
        if tag == 'DistinctParty':
            ident = attrs.get((None, 'FixedRef'), '').strip()
            if not ident or ident in self.ids or self.party is not None:
                raise ReadError(f'XML FixedRef欠落/重複: {ident}')
            self.ids.add(ident)
            self.party = ident
            self.party_start = len(self.out)
        elif tag == 'Identity' and self.party is not None:
            self.identity_false = _boolean(attrs, 'False')
        elif tag == 'Alias' and self.party is not None:
            if 'Identity' not in self.path:
                raise ReadError('XML AliasのIdentity欠落')
            self.alias = (_boolean(attrs,'Primary'), _boolean(attrs,'LowQuality'))
            self.alias_start=len(self.out)
        elif tag == 'DocumentedName' and self.alias is not None:
            self.parts = {}
            self.document = attrs.get((None, 'ID'), '')
        elif tag == 'NamePartValue' and self.document is not None:
            self.capture = attrs.get((None, 'ScriptID'), '')
            if not self.capture:
                raise ReadError('XML ScriptID欠落')
            self.buffer = []

    def characters(self, content):
        if self.capture is not None:
            self.buffer.append(content)

    def endElementNS(self, name, qname):
        tag = name[1]
        if tag == 'NamePartValue' and self.capture is not None:
            value = clean(''.join(self.buffer))
            if not value:
                raise ReadError('XML 空NamePartValue')
            self.parts.setdefault(self.capture, []).append(value)
            self.capture = None
        elif tag == 'DocumentedName' and self.document is not None:
            if not self.identity_false:
                if not self.parts:
                    raise ReadError(f'XML 名称解析不能: Party={self.party}')
                for script, parts in self.parts.items():
                    self.out.append(_row('OFAC',self.label,self.party,' '.join(parts),*self.alias,
                                         format='advanced_xml_v3', script_id=script,
                                         locator=f'Party:{self.party}/DocumentedName:{self.document}/Script:{script}',
                                         raw_record={'parts':parts,'primary':self.alias[0],'weak':self.alias[1]}))
            self.document = None
        elif tag == 'Alias' and self.alias is not None:
            if not self.identity_false and len(self.out)==self.alias_start:
                raise ReadError(f'XML 名称0件のAlias: Party={self.party}')
            self.alias = None
        elif tag == 'Identity':
            self.identity_false = False
        elif tag == 'DistinctParty':
            if self.party_start == len(self.out):
                raise ReadError(f'XML 名称0件のParty: {self.party}')
            self.party = None
        self.path.pop()


def read_ofac_xml(body: bytes, label: str) -> list[dict]:
    if b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():
        raise ReadError('XML DTD/ENTITYは禁止')
    handler = _Witness(label)
    parser = xml.sax.make_parser()
    parser.setFeature(feature_namespaces, True)
    parser.setContentHandler(handler)
    try:
        parser.parse(io.BytesIO(body))
    except xml.sax.SAXException as exc:
        raise ReadError(f'XML解析不能: {exc}') from exc
    if not handler.out:
        raise ReadError('XML Party/名称0件')
    return handler.out


_DESCRIPTOR = re.compile(r'生年月日|出生地|国籍|旅券|passport|original\s*script|\bDOB\b|\bPOB\b|Nationality|\d{4}\s*年\s*\d+\s*月\s*\d+\s*日\s*生', re.I)


def _cell_names(value):
    """Retain name parentheses; remove only explicit trailing descriptions."""
    chunks, start, stack = [], 0, []
    for pos, char in enumerate(value):
        if char in '(（':
            stack.append(pos)
        elif char in ')）':
            if not stack:
                raise ReadError(f'財務省名称括弧不整合: {value}')
            stack.pop()
        elif char in ';；' and not stack:
            chunks.append(value[start:pos]);start = pos + 1
    if stack:
        raise ReadError(f'財務省名称括弧不整合: {value}')
    chunks.append(value[start:])
    result = []
    for chunk in chunks:
        chunk = clean(chunk)
        while chunk.endswith((')', '）')):
            match = re.search(r'[(（]([^()（）]*)[)）]$', chunk)
            if not match or not _DESCRIPTOR.search(match[1]):
                break
            chunk = clean(chunk[:match.start()])
        if chunk not in NULLS and len(chunk) > 1:
            result.append(chunk)
    return result


def read_mof(body: bytes) -> list[dict]:
    rows = csv.reader(io.StringIO(decode(body)), strict=True)
    try:
        header = next(rows, [])
        if header != MOF_COLUMNS:
            raise ReadError('財務省CSV列構造変更')
        out, ids = [], set()
        for line, values in enumerate(rows, 2):
            if len(values) != len(header):
                raise ReadError(f'財務省CSV行構造変更: line={line}')
            raw = dict(zip(header, values))
            ident = clean(raw['番号'])
            if not ident or ident in ids or not clean(raw['区分']):
                raise ReadError(f'財務省番号欠落/重複: line={line}: {ident}')
            ids.add(ident)
            before = len(out)
            for field in MOF_NAME_COLUMNS:
                for name in _cell_names(raw[field]):
                    out.append(_row('財務省',raw['区分'],ident,name,field.startswith('氏名'),
                                    field.startswith('確定に十分'),locator=f'line:{line}/{field}',
                                    un_id=raw['国連参照番号'],field=field,raw_record=raw))
            # Explicit native-script labels only; no guessing from free prose.
            notice = raw['外務省告示情報']
            for marker in re.finditer(r'original\s*(?:script|scipt)\s*[:：]\s*',notice,re.I):
                tail = notice[marker.end():]
                end = re.search(r'[)）]|[(（](?:a\.k\.a|f\.k\.a|n\.k\.a)|(?:称号|役職|生年月日|出生地|国籍|旅券番号|ＩＤ番号|所在地)[:：]|国連制裁委員会',tail,re.I)
                if not end:
                    raise ReadError(f'財務省original script終端不明: {ident}')
                value = clean(tail[:end.start()]).strip(' (（)）;；,，、:：')
                if re.match(r'original\s*(?:script|scipt)\s*[:：]',value,re.I):
                    continue
                if value in NULLS or not re.search(r'[\u0370-\u052f\u0590-\u06ff\u0750-\u077f\u08a0-\u097f\u1100-\u11ff\u2de0-\u2dff\u3040-\u30ff\u3400-\u9fff\ua640-\ua69f\uac00-\ud7af\uf900-\ufeff]',value):
                    continue
                if not 2 <= len(value) <= 200:
                    raise ReadError(f'財務省original script長さ不明: {ident}')
                out.append(_row('財務省',raw['区分'],ident,value,False,locator=f'line:{line}/original_script',
                                un_id=raw['国連参照番号'],field='外務省告示情報',raw_record=raw))
            if len(out) == before:
                raise ReadError(f'財務省名称0件のレコード: {ident}')
    except csv.Error as exc:
        raise ReadError(f'財務省CSV解析不能: {exc}') from exc
    if not out:
        raise ReadError('財務省CSV名称0件')
    return out
