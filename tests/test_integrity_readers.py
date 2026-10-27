import csv
import io
import unittest
from pathlib import Path

from src import integrity_readers as R

NS = 'https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ADVANCED_XML'


def xml(party='1', extra='', false='false', low='false'):
    return f'''<Sanctions xmlns="{NS}" Version="3"><DistinctParties>
<DistinctParty FixedRef="{party}"><Profile><Identity ID="7" False="{false}">
<Alias Primary="true" LowQuality="{low}"><DocumentedName ID="8">
<DocumentedNamePart><NamePartValue ScriptID="1">Jane</NamePartValue></DocumentedNamePart>
<DocumentedNamePart><NamePartValue ScriptID="2">ジェーン</NamePartValue></DocumentedNamePart>
<DocumentedNamePart><NamePartValue ScriptID="1">Doe</NamePartValue></DocumentedNamePart>
</DocumentedName></Alias></Identity></Profile></DistinctParty>{extra}
</DistinctParties></Sanctions>'''.encode()


def mof_csv(**changes):
    row = {c: '' for c in R.MOF_COLUMNS}
    row.update({'区分':'9','番号':'009-000047','氏名（英語）':'MUHAMMED LUMISA'})
    row.update(changes)
    out = io.StringIO();w=csv.DictWriter(out,fieldnames=R.MOF_COLUMNS);w.writeheader();w.writerow(row)
    return out.getvalue().encode()


class ReaderTests(unittest.TestCase):
    def test_xml_never_mixes_scripts(self):
        names = R.read_ofac_xml(xml(), 'SDN')
        self.assertEqual([(r['name'],r['source_id'],r['primary'],r['weak']) for r in names],
                         [('Jane Doe','1',True,False),('ジェーン','1',True,False)])

    def test_xml_weak_preserved(self):
        self.assertTrue(all(r['weak'] for r in R.read_ofac_xml(xml(low='true'),'SDN')))

    def test_false_identity_cannot_satisfy_party_name_coverage(self):
        with self.assertRaises(R.ReadError): R.read_ofac_xml(xml(false='true'),'SDN')

    def test_xml_rejects_missing_id_duplicate_id_namespace_version_and_empty_names(self):
        party = '<DistinctParty FixedRef="1"/>'
        for body in [xml(party=''),xml(extra=party),xml().replace(b'Version="3"',b'Version="4"'),
                     xml().replace(NS.encode(),b'urn:changed'),xml().replace(b'NamePartValue',b'UnknownPart'),b'<html/>',b'']:
            with self.subTest(body=body[:90]), self.assertRaises(R.ReadError): R.read_ofac_xml(body,'SDN')

    def test_xml_rejects_unknown_quality_and_external_entities(self):
        for body in [xml(low='maybe'), b'<!DOCTYPE foo [<!ENTITY x SYSTEM "file:///etc/passwd">]>'+xml()]:
            with self.assertRaises(R.ReadError): R.read_ofac_xml(body,'SDN')

    def test_partial_schema_change_inside_a_valid_alias_is_blocked(self):
        body=xml().replace(b'</DocumentedName>',b'<UnknownNamePart>Lost</UnknownNamePart></DocumentedName>')
        with self.assertRaises(R.ReadError): R.read_ofac_xml(body,'SDN')

    def test_empty_alias_cannot_hide_behind_another_valid_alias(self):
        body=xml().replace(b'</Identity>',b'<Alias Primary="false" LowQuality="false"/></Identity>')
        with self.assertRaises(R.ReadError): R.read_ofac_xml(body,'SDN')

    def test_renamed_xml_only_alias_cannot_disappear_behind_valid_alias(self):
        extra=b'<AlternateAlias Primary="false" LowQuality="false"><DocumentedName ID="9"><DocumentedNamePart><NamePartValue ScriptID="1">UNSEEN NAME</NamePartValue></DocumentedNamePart></DocumentedName></AlternateAlias>'
        with self.assertRaises(R.ReadError):
            R.read_ofac_xml(xml().replace(b'</Identity>',extra+b'</Identity>'),'SDN')

    def test_classic_retains_party_and_primary_and_alias_roles(self):
        prim = b'1,"DOE, Jane",individual,SDGT,-0-,-0-,-0-,-0-,-0-,-0-,-0-,-0-\n\x1a'
        alt = b'1,99,aka,"Jane Other",-0-\n'
        self.assertEqual(R.read_classic(prim,'SDN')[0]['name'],'DOE, Jane')
        self.assertFalse(R.read_classic(alt,'SDN',alias=True)[0]['primary'])
        for body in [b'<html/>',b'1,Jane\n',b'1,Jane\n2\n',b'']:
            with self.assertRaises(R.ReadError): R.read_classic(body,'SDN')

    def test_mof_four_names_are_three_parties_with_one_strong_alias(self):
        names = R.read_mof(Path('tests/fixtures/integrity/mof_four_names.csv').read_bytes())
        expected = {'SEBASTIEN UWIMBABAZI':('009-000046',True),
                    'MUHAMMED LUMISA':('009-000047',True),
                    'LUMISA, MUHAMAD':('009-000047',False),
                    'CHARLES SEMATAMA':('009-000048',True)}
        actual = {r['name']:(r['source_id'],r['primary']) for r in names}
        for name, identity in expected.items(): self.assertEqual(actual[name],identity)

    def test_mof_does_not_split_descriptor_semicolons_or_surname_comma(self):
        rows=R.read_mof(mof_csv(**{'別名・別称（英語）':'LUMISA, MUHAMAD (DOB 1960; 1963); Other Name'}))
        self.assertEqual([r['name'] for r in rows],['MUHAMMED LUMISA','LUMISA, MUHAMAD','Other Name'])

    def test_mof_bad_structure_is_not_silently_skipped(self):
        for body in [mof_csv(**{'番号':''}),mof_csv(**{'氏名（英語）':''}),
                     mof_csv(**{'別名・別称（英語）':'Other (DOB 1960'}),
                     mof_csv().replace('氏名（英語）'.encode(),b'changed'),b'']:
            with self.subTest(body=body[:70]),self.assertRaises(R.ReadError): R.read_mof(body)

    def test_mof_original_script_has_explicit_field_boundary(self):
        rows=R.read_mof(mof_csv(**{'外務省告示情報':'Name (original script: Иван Иванов) 生年月日：1960'}))
        self.assertIn('Иван Иванов',[r['name'] for r in rows])
        with self.assertRaises(R.ReadError):
            R.read_mof(mof_csv(**{'外務省告示情報':'original script: Иван Иванов no terminator'}))


if __name__ == '__main__': unittest.main()
