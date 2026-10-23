"""Offline candidates from explicit OFAC Individual name-part declarations.

These are derived representations, never newly listed aliases or proof of a
master party's identity. The ingestion parser and global key rules are unused.
"""
import io
import xml.sax
from collections import Counter, defaultdict
from xml.sax.handler import ContentHandler, feature_namespaces

from .integrity_readers import NAMESPACE, ReadError, clean


def _boolean(attrs, field):
    value = attrs.get((None, field), 'false')
    if value not in {'true', 'false'}:
        raise ReadError('OFAC name-part boolean is unknown: ' + field)
    return value == 'true'


class _PersonForms(ContentHandler):
    def __init__(self, label):
        super().__init__()
        self.label = label
        self.path = []
        self.types, self.party_types, self.subtypes = {}, {}, {}
        self.party = self.profile = self.identity = self.alias = self.document = None
        self.capture = None
        self.forms = []
        self.skipped = Counter()

    def startElementNS(self, name, qname, attrs):
        namespace, tag = name
        if namespace != NAMESPACE:
            raise ReadError('OFAC name-part namespace changed')
        if not self.path and (tag != 'Sanctions' or attrs.get((None, 'Version')) != '3'):
            raise ReadError('OFAC name-part root/version changed')
        parent = self.path[-1] if self.path else None
        allowed = {'DocumentedName': {'DocumentedNamePart'},
                   'DocumentedNamePart': {'NamePartValue'}, 'NamePartValue': set(),
                   'NamePartTypeValues': {'NamePartType'}, 'NamePartType': set(),
                   'PartyTypeValues': {'PartyType'}, 'PartyType': set(),
                   'PartySubTypeValues': {'PartySubType'}, 'PartySubType': set(),
                   'NamePartGroups': {'MasterNamePartGroup'},
                   'MasterNamePartGroup': {'NamePartGroup'}, 'NamePartGroup': set()}
        if parent in allowed and tag not in allowed[parent]:
            raise ReadError('OFAC typed-name child structure changed: ' + parent + '/' + tag)
        parents = {'NamePartTypeValues': 'ReferenceValueSets', 'NamePartType': 'NamePartTypeValues',
                   'PartyTypeValues': 'ReferenceValueSets', 'PartyType': 'PartyTypeValues',
                   'PartySubTypeValues': 'ReferenceValueSets', 'PartySubType': 'PartySubTypeValues',
                   'NamePartGroups': 'Identity', 'MasterNamePartGroup': 'NamePartGroups',
                   'NamePartGroup': 'MasterNamePartGroup'}
        if tag in parents and parent != parents[tag]:
            raise ReadError('OFAC typed-name declaration parent changed: ' + tag)
        self.path.append(tag)
        if tag in {'NamePartType', 'PartyType'} and 'ReferenceValueSets' in self.path:
            ident = attrs.get((None, 'ID'), '')
            if not ident:
                raise ReadError('OFAC name-part reference ID missing')
            self.capture = dict(kind=tag, ident=ident, text=[])
        elif tag == 'PartySubType' and 'ReferenceValueSets' in self.path:
            ident, parent = attrs.get((None, 'ID'), ''), attrs.get((None, 'PartyTypeID'), '')
            if not ident or not parent or (ident in self.subtypes and self.subtypes[ident] != parent):
                raise ReadError('OFAC party subtype declaration is missing/conflicting')
            self.subtypes[ident] = parent
        elif tag == 'DistinctParty':
            self.party = attrs.get((None, 'FixedRef'), '')
            if not self.party:
                raise ReadError('OFAC typed-name party ID missing')
        elif tag == 'Profile' and self.party is not None:
            self.profile = attrs.get((None, 'PartySubTypeID'), '')
        elif tag == 'Identity' and self.party is not None:
            self.identity = dict(false=_boolean(attrs, 'False'), groups={}, documents=[])
        elif tag == 'Alias' and self.identity is not None:
            self.alias = dict(primary=_boolean(attrs, 'Primary'), weak=_boolean(attrs, 'LowQuality'))
        elif tag == 'DocumentedName' and self.alias is not None:
            self.document = dict(ident=attrs.get((None, 'ID'), ''), scripts=defaultdict(list), **self.alias)
            if not self.document['ident']:
                raise ReadError('OFAC typed-name DocumentedName ID missing')
        elif tag == 'NamePartValue' and self.document is not None:
            self.capture = dict(kind=tag, group=attrs.get((None, 'NamePartGroupID'), ''),
                                script=attrs.get((None, 'ScriptID'), ''), text=[])
        elif tag == 'NamePartGroup' and self.identity is not None and 'NamePartGroups' in self.path:
            ident, part_type = attrs.get((None, 'ID'), ''), attrs.get((None, 'NamePartTypeID'), '')
            if not ident or not part_type:
                raise ReadError('OFAC name-part group declaration missing')
            groups = self.identity['groups']
            if ident in groups and groups[ident] != part_type:
                raise ReadError('OFAC name-part group declaration conflicts')
            groups[ident] = part_type

    def characters(self, text):
        if self.capture is not None:
            self.capture['text'].append(text)

    def endElementNS(self, name, qname):
        tag = name[1]
        if self.capture is not None and tag == self.capture['kind']:
            captured = self.capture
            text = clean(''.join(captured['text']))
            self.capture = None
            if tag == 'NamePartValue':
                if not captured['script'] or not text:
                    raise ReadError('OFAC name-part script/value missing')
                self.document['scripts'][captured['script']].append(dict(group_id=captured['group'], value=text))
            else:
                target = self.types if tag == 'NamePartType' else self.party_types
                ident = captured['ident']
                if not text or (ident in target and target[ident] != text):
                    raise ReadError('OFAC name-part reference declaration conflicts')
                target[ident] = text
        if tag == 'DocumentedName' and self.document is not None:
            self.identity['documents'].append(self.document)
            self.document = None
        elif tag == 'Alias':
            self.alias = None
        elif tag == 'Identity' and self.identity is not None:
            self.emit_identity()
            self.identity = None
        elif tag == 'Profile':
            self.profile = None
        elif tag == 'DistinctParty':
            self.party = None
        self.path.pop()

    def emit_identity(self):
        if self.identity['false']:
            self.skipped['false_identity'] += len(self.identity['documents'])
            return
        party_type = self.party_types.get(self.subtypes.get(self.profile))
        if party_type != 'Individual':
            self.skipped['not_declared_individual'] += len(self.identity['documents'])
            return
        for document in self.identity['documents']:
            for script, raw_parts in document['scripts'].items():
                parts = []
                for part in raw_parts:
                    type_id = self.identity['groups'].get(part['group_id'])
                    parts.append(dict(part, type_id=type_id, type=self.types.get(type_id)))
                by_type = defaultdict(list)
                for part in parts:
                    by_type[part['type']].append(part)
                allowed = {'First Name', 'Middle Name', 'Last Name'}
                if not set(by_type) <= allowed:
                    self.skipped['unsupported_or_unresolved_part_type'] += 1
                    continue
                if len(by_type['First Name']) != 1 or len(by_type['Last Name']) != 1 or len(by_type['Middle Name']) > 1:
                    self.skipped['incomplete_or_ambiguous_parts'] += 1
                    continue
                ordered = by_type['First Name'] + by_type['Middle Name'] + by_type['Last Name']
                self.forms.append(dict(
                    source='OFAC', category=self.label, source_id=self.party,
                    name=' '.join(p['value'] for p in ordered),
                    primary=document['primary'], weak=document['weak'],
                    field='person_name_parts', transform='given_middle_family', script_id=script,
                    name_parts=ordered,
                    locator=f'Party:{self.party}/DocumentedName:{document["ident"]}/Script:{script}/typed-order',
                ))


def read_person_forms(body, label):
    """Require explicit Individual, field definitions and one script per form.

    Unsupported or incomplete renderings are counted, not guessed. Structural
    XML failures raise, so the caller can save a blocked audit. No network I/O.
    """
    if b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():
        raise ReadError('OFAC typed-name DTD/ENTITY forbidden')
    handler = _PersonForms(label)
    parser = xml.sax.make_parser()
    parser.setFeature(feature_namespaces, True)
    parser.setContentHandler(handler)
    try:
        parser.parse(io.BytesIO(body))
    except xml.sax.SAXException as exc:
        raise ReadError('OFAC typed-name XML parse failed: ' + str(exc)) from exc
    return dict(forms=handler.forms, skipped_counts=dict(sorted(handler.skipped.items())))
