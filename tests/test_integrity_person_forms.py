"""Typed OFAC name evidence must never guess identity or promote weak names."""
import copy
import gzip
import hashlib
import tempfile
import unittest
from pathlib import Path

from src import integrity_triage as T
from tests import test_integrity_audit as F
from src.integrity_person_forms import read_person_forms
from src.integrity_readers import ReadError

NS = 'https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ADVANCED_XML'


def document(last='Peijnenburg', first='Aam', middle='', *, weak='true', false='false', party_type='1', extra=''):
    mid = f'<DocumentedNamePart><NamePartValue ScriptID="215" NamePartGroupID="3">{middle}</NamePartValue></DocumentedNamePart>' if middle else ''
    return f'''<Sanctions xmlns="{NS}" Version="3"><ReferenceValueSets>
<NamePartTypeValues><NamePartType ID="1520">Last Name</NamePartType><NamePartType ID="1521">First Name</NamePartType><NamePartType ID="1522">Middle Name</NamePartType></NamePartTypeValues>
<PartyTypeValues><PartyType ID="1">Individual</PartyType><PartyType ID="2">Entity</PartyType></PartyTypeValues>
<PartySubTypeValues><PartySubType ID="4" PartyTypeID="{party_type}">Unknown</PartySubType></PartySubTypeValues>
</ReferenceValueSets><DistinctParties><DistinctParty FixedRef="39585"><Profile PartySubTypeID="4">
<Identity ID="31209" False="{false}"><Alias Primary="false" LowQuality="{weak}"><DocumentedName ID="58349">
<DocumentedNamePart><NamePartValue ScriptID="215" NamePartGroupID="1">{last}</NamePartValue></DocumentedNamePart>
<DocumentedNamePart><NamePartValue ScriptID="215" NamePartGroupID="2">{first}</NamePartValue></DocumentedNamePart>{mid}{extra}
</DocumentedName></Alias><NamePartGroups><MasterNamePartGroup><NamePartGroup ID="1" NamePartTypeID="1520"/></MasterNamePartGroup>
<MasterNamePartGroup><NamePartGroup ID="2" NamePartTypeID="1521"/></MasterNamePartGroup><MasterNamePartGroup><NamePartGroup ID="3" NamePartTypeID="1522"/></MasterNamePartGroup>
</NamePartGroups></Identity></Profile></DistinctParty></DistinctParties></Sanctions>'''.encode()


class PersonFormEvidenceTests(unittest.TestCase):
    def collect(self, body, target):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            relative = 'data/raw/ofac_sdn_advanced/current.xml.gz'
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_bytes(gzip.compress(body, mtime=0))
            report = dict(documents=[dict(source='ofac_sdn', role='advanced', raw_path=relative,
                                         source_url='https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.XML',
                                         sha256=hashlib.sha256(body).hexdigest())])
            return T.collect_witnesses(root, report, {target})

    def test_weak_given_family_form_has_original_party_quality_and_parts(self):
        proofs, manifest, errors = self.collect(document(), 'aampeijnenburg')
        self.assertEqual(errors, [])
        self.assertTrue(proofs.get('aampeijnenburg'), 'Typed given/family evidence was omitted')
        proof = proofs['aampeijnenburg'][0]
        self.assertEqual((proof['name'], proof['source_id'], proof['weak'], proof['current']), ('Aam Peijnenburg', '39585', True, True))
        self.assertEqual(proof['field'], 'person_name_parts')
        self.assertEqual([p['type'] for p in proof['name_parts']], ['First Name', 'Last Name'])

    def test_ab_prefix_does_not_hide_explicit_first_middle_last_fields(self):
        proofs, _, _ = self.collect(document(last='Kadkhodaee', first='Abbas', middle='Ali', weak='false'), 'abbasalikadkhodaee')
        self.assertTrue(proofs.get('abbasalikadkhodaee'), 'AB prefix was treated as corporate suffix')
        self.assertFalse(proofs['abbasalikadkhodaee'][0]['weak'])

    def test_typed_form_is_review_candidate_without_manufacturing_literal_alias(self):
        proof = dict(name='Jane Doe', source_id='1', category='SDN', current=True,
                     weak=False, primary=False, field='person_name_parts', transform='given_middle_family',
                     name_parts=[dict(type='First Name', value='Jane'), dict(type='Last Name', value='Doe')])
        report = dict(status='BLOCKED', findings=[dict(kind='EXTRA_REGISTRATION', checked_source='OFAC', master=F.master())])
        item = T.classify(report, [F.master()], [], {'janedoe': [proof]})['items'][0]
        self.assertEqual(item['classification'], 'CURRENT_PERSON_ORDER_VARIANT')
        self.assertTrue(item['retention_eligible'])
        self.assertFalse(item['approved'])

    def test_strong_typed_relation_cannot_be_ignored_when_classifying_weak_only(self):
        weak = dict(name='Jane Doe', source_id='1', category='SDN', current=True, weak=True,
                    primary=False, field='name', transform='exact')
        strong = dict(weak, source_id='2', weak=False, field='person_name_parts', transform='given_middle_family',
                      name_parts=[dict(type='First Name', value='Jane'), dict(type='Last Name', value='Doe')])
        report = dict(status='BLOCKED', findings=[dict(kind='WEAK_ALIAS_ACTIVE', master=F.master(), official=dict(source='OFAC'))])
        item = T.classify(report, [F.master()], [], {'janedoe': [weak, strong]})['items'][0]
        self.assertFalse(item['retention_eligible'])
        self.assertEqual(item['classification'], 'WEAK_EVIDENCE_CONFLICT')

    def test_entity_and_unknown_field_definitions_are_not_reordered(self):
        bodies = [document(party_type='2'), document().replace(b'>Last Name<', b'>Unrecognized Name Type<')]
        for body in bodies:
            with self.subTest(body=body[:70]):
                proofs, _, _ = self.collect(body, 'aampeijnenburg')
                self.assertNotIn('aampeijnenburg', proofs)

    def test_distinct_scripts_are_never_joined_into_a_given_family_form(self):
        body = document().replace(b'ScriptID="215" NamePartGroupID="2"', b'ScriptID="999" NamePartGroupID="2"')
        proofs, _, _ = self.collect(body, 'aampeijnenburg')
        self.assertNotIn('aampeijnenburg', proofs)

    def test_historical_typed_fields_are_not_claimed_as_current(self):
        # The existing historical witness is not eligible as a CURRENT form.
        proof = dict(name='Jane Doe', source_id='1', category='SDN', current=False,
                     weak=False, primary=False, field='person_name_parts', transform='given_middle_family')
        report = dict(status='BLOCKED', findings=[dict(kind='EXTRA_REGISTRATION', checked_source='OFAC', master=F.master())])
        item = T.classify(report, [F.master()], [], {'janedoe': [proof]})['items'][0]
        self.assertFalse(item['retention_eligible'])

    def test_false_identity_and_ambiguous_first_names_never_generate_forms(self):
        duplicate = '<DocumentedNamePart><NamePartValue ScriptID="215" NamePartGroupID="2">Other</NamePartValue></DocumentedNamePart>'
        for body in [document(false='true'), document(extra=duplicate)]:
            result = read_person_forms(body, 'SDN')
            self.assertEqual(result['forms'], [])
            self.assertTrue(result['skipped_counts'])

    def test_unknown_name_child_or_missing_document_id_is_not_partially_accepted(self):
        unknown = document().replace(b'</DocumentedName>', b'<UnknownNamePart>Lost</UnknownNamePart></DocumentedName>')
        missing_id = document().replace(b'<DocumentedName ID="58349">', b'<DocumentedName>')
        for body in [unknown, missing_id]:
            with self.subTest(body=body[:70]), self.assertRaises(ReadError):
                read_person_forms(body, 'SDN')

    def test_xml_namespace_boolean_version_and_dtd_fail_closed(self):
        for body in [document(weak='maybe'), document().replace(NS.encode(), b'urn:unknown'),
                     document().replace(b'Version="3"', b'Version="4"'), b'<!DOCTYPE x>'+document(), b'<html/>']:
            with self.subTest(body=body[:70]), self.assertRaises(ReadError):
                read_person_forms(body, 'SDN')

    def test_homonyms_retain_all_distinct_party_relations(self):
        body = document()
        start = body.index(b'<DistinctParty FixedRef=')
        end = body.index(b'</DistinctParty>') + len(b'</DistinctParty>')
        second = body[start:end].replace(b'FixedRef="39585"', b'FixedRef="99"')
        body = body.replace(b'</DistinctParties>', second + b'</DistinctParties>')
        proofs, _, _ = self.collect(body, 'aampeijnenburg')
        self.assertEqual({p['source_id'] for p in proofs['aampeijnenburg']}, {'39585', '99'})

    def test_weak_typed_evidence_stays_weak_and_changed_parts_invalidate_review_id(self):
        proof = dict(name='Jane Mary Doe', source_id='1', category='SDN', current=True,
                     weak=True, primary=False, field='person_name_parts', transform='given_middle_family',
                     name_parts=[dict(type='First Name', value='Jane'), dict(type='Middle Name', value='Mary'), dict(type='Last Name', value='Doe')])
        master = F.master('Jane Mary Doe', 'janemarydoe')
        report = dict(status='BLOCKED', findings=[dict(kind='EXTRA_REGISTRATION', checked_source='OFAC', master=master)])
        one = T.classify(report, [master], [], {'janemarydoe': [proof]})['items'][0]
        self.assertEqual(one['classification'], 'CURRENT_WEAK_PERSON_ORDER_VARIANT')
        self.assertFalse(one['approved'])
        changed = dict(proof, name_parts=[dict(type='First Name', value='Jane Mary'), dict(type='Last Name', value='Doe')])
        two = T.classify(report, [master], [], {'janemarydoe': [changed]})['items'][0]
        self.assertNotEqual(one['review_id'], two['review_id'])

    def test_unknown_wrappers_and_nested_reference_markup_do_not_produce_evidence(self):
        wrapped = document().replace(b'<NamePartGroups>', b'<NamePartGroups><UnexpectedContainer>').replace(
            b'</NamePartGroups>', b'</UnexpectedContainer></NamePartGroups>')
        nested = document().replace(b'>First Name<', b'>First <Unexpected/>Name<')
        for body in [wrapped, nested]:
            with self.subTest(body=body[:70]), self.assertRaises(T.TriageError):
                self.collect(body, 'aampeijnenburg')

    def test_explicit_retention_entry_cannot_release_a_weak_strong_party_conflict(self):
        from datetime import datetime, timezone
        from src import integrity_decisions as D
        weak = dict(name='Jane Doe', source_id='1', category='SDN', current=True, weak=True,
                    primary=False, field='name', transform='exact')
        strong = dict(weak, source_id='2', weak=False, field='person_name_parts', transform='given_middle_family',
                      name_parts=[dict(type='First Name', value='Jane'), dict(type='Last Name', value='Doe')])
        report = dict(status='BLOCKED', findings=[dict(kind='WEAK_ALIAS_ACTIVE', blocking=True, master=F.master(), official=dict(source='OFAC'))])
        triage = T.classify(report, [F.master()], [], {'janedoe': [weak, strong]})
        item = triage['items'][0]
        entry = dict(review_id=item['review_id'], decision='APPROVED_RETAIN', reviewed_by='fixture',
                     reviewed_at='2026-01-01T00:00:00Z', expires_at='2030-01-01T00:00:00Z',
                     reason='Attempted retention of ambiguous name', reviewed_evidence_sha256=item['evidence_sha256'])
        result = D.apply(report, triage, dict(schema_version=1, entries=[entry]), now=datetime(2026, 10, 24, tzinfo=timezone.utc))
        self.assertTrue(result['findings'][0]['blocking'])
        self.assertEqual(result['retention_review'][0]['status'], 'INELIGIBLE')

    def test_actual_frozen_person_fields_preserve_ids_and_weak_roles(self):
        import json
        fixture = Path('tests/fixtures/integrity/ofac_person_parts.xml')
        body = fixture.read_bytes()
        provenance = json.loads(fixture.with_suffix('.provenance.json').read_text())
        self.assertEqual(hashlib.sha256(body).hexdigest(), provenance['fragment_sha256'])
        forms = read_person_forms(body, 'SDN')['forms']
        aam = [x for x in forms if x['name'] == 'Aam Peijnenburg']
        abbas = [x for x in forms if x['name'] == 'Abbas Ali Kadkhodaee']
        self.assertEqual([(x['source_id'], x['weak'], x['primary']) for x in aam], [('39585', True, False)])
        self.assertEqual([(x['source_id'], x['weak'], x['primary']) for x in abbas], [('28545', False, False)])


class TypedGateFailureTests(unittest.TestCase):
    setUp = F.AuditIOTests.setUp

    def test_conflicting_metadata_blocks_a_healthy_core_audit_and_saves_failure(self):
        import json
        from tests import test_integrity_publication as G
        from src import integrity_audit as A
        G.GateTests.install_ofac(self)
        path = self.root / 'data/raw/ofac_sdn/advanced.gz'
        body = gzip.decompress(path.read_bytes()).replace(
            b'</Identity>', b'<NamePartGroups><MasterNamePartGroup><NamePartGroup ID="1" NamePartTypeID="1520"/>'
            b'<NamePartGroup ID="1" NamePartTypeID="1521"/></MasterNamePartGroup></NamePartGroups></Identity>')
        path.write_bytes(gzip.compress(body, mtime=0))
        state_path = self.root / 'data/state.json'
        state = json.loads(state_path.read_text())
        state['ofac_sdn']['advanced_sha256'] = hashlib.sha256(body).hexdigest()
        state_path.write_text(json.dumps(state))
        self.assertEqual(A.audit(self.root, ['ofac'])['status'], 'PASS')
        result = G.GateTests.run_gate(self, 'success', 'ofac')
        self.assertNotEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertFalse((self.root/'published').exists())
        reports = [p for p in self.root.glob('data/integrity_audit/*.json.gz') if not p.name.startswith('triage-')]
        self.assertEqual(len(reports), 1)
        report = json.loads(gzip.decompress(reports[0].read_bytes()))
        self.assertIn('TRIAGE_FAILED', report['counts'])

    def test_unknown_reference_markup_stops_gate_and_preserves_audit_artifact(self):
        import json
        from tests import test_integrity_publication as G
        from src import integrity_audit as A
        G.GateTests.install_ofac(self)
        path = self.root/'data/raw/ofac_sdn/advanced.gz'
        body = gzip.decompress(path.read_bytes()).replace(b'<DistinctParties>',
            b'<ReferenceValueSets><NamePartTypeValues><NamePartType ID="1521">First <Unexpected/>Name</NamePartType></NamePartTypeValues></ReferenceValueSets><DistinctParties>')
        path.write_bytes(gzip.compress(body, mtime=0))
        state_path = self.root/'data/state.json';state=json.loads(state_path.read_text())
        state['ofac_sdn']['advanced_sha256']=hashlib.sha256(body).hexdigest();state_path.write_text(json.dumps(state))
        self.assertEqual(A.audit(self.root, ['ofac'])['status'], 'PASS')
        result=G.GateTests.run_gate(self, 'success', 'ofac')
        self.assertNotEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertFalse((self.root/'published').exists())
        files=[p for p in self.root.glob('data/integrity_audit/*.json.gz') if not p.name.startswith('triage-')]
        self.assertEqual(len(files), 1)
        self.assertIn('TRIAGE_FAILED', json.loads(gzip.decompress(files[0].read_bytes()))['counts'])


if __name__ == '__main__':
    unittest.main()
