import unittest

from src import un_sources as U


def xml(name='Alice Example', alias='Ally', quality='Good', *, ref='QDi.001', comment='note'):
    return f'''<CONSOLIDATED_LIST dateGenerated="2026-10-27T00:00:00Z">
    <INDIVIDUALS><INDIVIDUAL><DATAID>1</DATAID><FIRST_NAME>{name}</FIRST_NAME>
    <UN_LIST_TYPE>Al-Qaida</UN_LIST_TYPE><REFERENCE_NUMBER>{ref}</REFERENCE_NUMBER>
    <LISTED_ON>2020-01-01</LISTED_ON><COMMENTS1>{comment}</COMMENTS1>
    <NAME_ORIGINAL_SCRIPT>アリス</NAME_ORIGINAL_SCRIPT>
    <INDIVIDUAL_ALIAS><QUALITY>{quality}</QUALITY><ALIAS_NAME>{alias}</ALIAS_NAME></INDIVIDUAL_ALIAS>
    </INDIVIDUAL></INDIVIDUALS><ENTITIES><ENTITY><DATAID>2</DATAID>
    <FIRST_NAME>Example Ltd</FIRST_NAME><UN_LIST_TYPE>Al-Qaida</UN_LIST_TYPE>
    <REFERENCE_NUMBER>QDe.001</REFERENCE_NUMBER><LISTED_ON>2020-01-02</LISTED_ON>
    <ENTITY_ALIAS><QUALITY>f.k.a.</QUALITY><ALIAS_NAME>Old Ltd</ALIAS_NAME></ENTITY_ALIAS>
    </ENTITY></ENTITIES></CONSOLIDATED_LIST>'''.encode()


PRESS = b'''<html><h1>Press Releases</h1><main>
<div class="view-press-releases"><table><tbody><tr><td>
<time datetime="2026-10-26T12:00:00Z">26 October 2026</time></td><td>
<a href="https://press.un.org/en/2026/sc16488.doc.htm">Committee removes three entries</a>
</td></tr></tbody></table></div></main></html>'''



UPDATES = b'''<html><h1>List of updates to the UNSC Consolidated List</h1>
<table><thead><tr><th>Date</th><th>Sanctions list</th>
<th>Permanent reference number</th><th>Type of update</th>
<th>Number of entries affected</th><th>Links to notification in English</th></tr></thead>
<tbody>
<tr><td>26/10/2026</td><td>1267</td><td>QDi.340, QDi.342, QDi.376</td>
<td>Amendment</td><td>3</td><td><a href="https://press.un.org/en/2026/sc16490.doc.htm">SC/16490</a></td></tr>
<tr><td>24/10/2026</td><td>1267</td><td>QDi.338, QDi.399, QDi.400</td>
<td>Delisting</td><td>3</td><td><a href="https://press.un.org/en/2026/sc16488.doc.htm">SC/16488</a></td></tr>
<tr><td>15/10/2026</td><td>1267</td><td>QDi.082, QDi.427, QDe.132</td>
<td>Amendment</td><td>3</td><td>SC/16480</td></tr>
<tr><td>05/10/2026</td><td>1518</td><td>IQe.200</td>
<td>De-Listing</td><td>1</td><td>SC/16468</td></tr>
<tr><td>09/12/2025</td><td>1518</td><td>IQi.074</td>
<td>Delisting</td><td>1</td><td>SC/16240</td></tr>
</tbody></table></html>'''


class UNSourceTests(unittest.TestCase):
    def test_official_update_history_is_record_aware(self):
        from datetime import datetime, timezone
        from src import un_updates as V
        items = V.parse_updates(UPDATES, now=datetime(2026,10,30,tzinfo=timezone.utc))
        self.assertEqual(len(items), 4)
        self.assertEqual(items[0]['references'], ['QDi.340', 'QDi.342', 'QDi.376'])
        self.assertEqual(items[0]['change'], 'AMENDMENT')
        self.assertEqual(items[1]['references'], ['QDi.338', 'QDi.399', 'QDi.400'])
        self.assertEqual(items[1]['change'], 'DELISTING')
        self.assertEqual(items[1]['notice_url'], 'https://press.un.org/en/2026/sc16488.doc.htm')
        self.assertNotEqual(items[0]['event_id'], items[1]['event_id'])
        self.assertEqual(items, V.parse_updates(UPDATES, now=datetime(2026,10,30,tzinfo=timezone.utc)))

    def test_updates_history_rejects_schema_loss_false_counts_and_untrusted_links(self):
        from datetime import datetime, timezone
        from src import un_updates as V
        now = datetime(2026,10,30,tzinfo=timezone.utc)
        variants = [
            b'<html>No update table</html>',
            UPDATES.replace(b'Permanent reference number', b'Reference removed'),
            UPDATES.replace(b'<td>3</td>', b'<td>2</td>', 1),
            UPDATES.replace(b'QDi.338', b'QDi.340'),
            UPDATES.replace(b'https://press.un.org/en/2026/sc16488.doc.htm',
                            b'https://un.org.evil.example/sc16488'),
            UPDATES.replace(b'26/10/2026', b'32/10/2026'),
        ]
        for body in variants:
            with self.subTest(change=body[:50]), self.assertRaises(U.UNSchemaError):
                V.parse_updates(body, now=now)

    def test_primary_original_alias_quality_and_entity_former_name(self):
        data = U.parse_xml(xml())
        party = data['parties']['QDi.001']
        self.assertEqual(party['data_id'], '1')
        self.assertEqual(party['committee'], '1267')
        self.assertEqual(party['names'], [
            {'name': 'Alice Example', 'kind': 'primary', 'weak': False},
            {'name': 'アリス', 'kind': 'original', 'weak': False},
            {'name': 'Ally', 'kind': 'alias', 'weak': False},
        ])
        self.assertEqual(data['parties']['QDe.001']['names'][1],
                         {'name': 'Old Ltd', 'kind': 'former', 'weak': False})
        low = U.parse_xml(xml(quality='Low'))['parties']['QDi.001']['names'][-1]
        self.assertTrue(low['weak'])

    def test_metadata_amendment_changes_party_fingerprint(self):
        a = U.parse_xml(xml())['parties']['QDi.001']['fingerprint']
        b = U.parse_xml(xml(comment='new passport'))['parties']['QDi.001']['fingerprint']
        self.assertNotEqual(a, b)
        self.assertEqual(a, U.parse_xml(xml().replace(b'><', b'>\n <'))['parties']['QDi.001']['fingerprint'])

    def test_xsd_date_with_timezone_is_preserved(self):
        party = U.parse_xml(xml().replace(b'2020-01-01', b'2015-03-27-04:00'))['parties']['QDi.001']
        self.assertEqual(party['listed_on'], '2015-03-27-04:00')

    def test_structural_failures_never_become_an_empty_list(self):
        cases = [b'<html>Access denied</html>', b'<CONSOLIDATED_LIST/>',
                 xml(quality='Unknown'), xml(ref='ZZi.001'),
                 xml().replace(b'<DATAID>2</DATAID>', b'<DATAID>1</DATAID>'),
                 xml().replace(b'QDe.001', b'QDi.001'),
                 xml().replace(b'2020-01-01', b'not a date'),
                 b'<!DOCTYPE X [<!ENTITY x "a">]>' + xml(),
                 xml().replace(b'<FIRST_NAME>Alice Example</FIRST_NAME>', b'<FIRST_NAME/>')]
        for body in cases:
            with self.subTest(body=body[:70]), self.assertRaises(U.UNSchemaError):
                U.parse_xml(body)

    def test_official_redirect_allowlist_and_no_broad_blob_access(self):
        for url in ['https://scsanctions.un.org/resources/xml/en/name/consolidated.xml',
                    'https://unsolprodfiles.blob.core.windows.net/publiclegacyxmlfiles/EN/consolidatedLegacyByNAME.xml?sig=secret']:
            self.assertEqual(U.validate_un_url(url), url)
        for url in ['http://main.un.org/x', 'https://un.org.evil.test/x',
                    'https://other.blob.core.windows.net/x', 'https://user@main.un.org/x',
                    'https://main.un.org:443/x', 'https://127.0.0.1/x']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                U.validate_un_url(url)

    def test_press_index_detects_notices_but_navigation_is_not_a_notice(self):
        rows = U.parse_notices(PRESS, U.press_url('1267'))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['url'], 'https://press.un.org/en/2026/sc16488.doc.htm')
        self.assertEqual(rows[0]['publication_at'], '2026-10-26T12:00:00Z')
        self.assertEqual(rows[0]['title'], 'Committee removes three entries')
        french = U.parse_notices(PRESS.replace(b'/en/2026/', b'/fr/2026/'), U.press_url('1267'))
        self.assertEqual(french[0]['url'], 'https://press.un.org/fr/2026/sc16488.doc.htm')
        for body in [b'<h1>Press Releases</h1><nav>menu</nav>',
                     PRESS.replace(b'https://press.un.org', b'https://evil.test'),
                     PRESS.replace(b'2026-10-26T12:00:00Z', b'bad date')]:
            with self.subTest(body=body[:80]), self.assertRaises(U.UNSchemaError):
                U.parse_notices(body, U.press_url('1267'))

    def test_official_legacy_and_irregular_press_links_and_duplicate_rows(self):
        for old in ['https://www.un.org/press/en/2022/sc14895.doc.htm',
                    'https://un.org/press/en/2026/sc16301.doc.htm',
                    'https://press.un.org/en/2024/sc15921',
                    'https://press.un.org/2025/sc16182.doc.htm']:
            body = PRESS.replace(b'https://press.un.org/en/2026/sc16488.doc.htm', old.encode())
            self.assertEqual(U.parse_notices(body, U.press_url('1267'))[0]['url'], old)
        row = PRESS.split(b'<tr>')[1].split(b'</tr>')[0]
        self.assertEqual(len(U.parse_notices(PRESS.replace(b'</tbody>', b'<tr>' + row + b'</tr></tbody>'),
                                           U.press_url('1267'))), 1)
        alternative = row.replace(b'Committee removes three entries', b'Committee considers a report')
        combined = U.parse_notices(PRESS.replace(b'</tbody>', b'<tr>' + alternative + b'</tr></tbody>'), U.press_url('1267'))
        self.assertIn('Committee removes three entries', combined[0]['title'])
        self.assertIn('Committee considers a report', combined[0]['title'])
        body = PRESS.replace(b'<tr><td>', b'<tr><td><a href="/securitycouncil/en/content/sc13673">SC/13673</a>')
        for missing in [b'', b'http://www.un.org/press/en/2018/sc13471.doc.htm']:
            replaced = body.replace(b'https://press.un.org/en/2026/sc16488.doc.htm', missing)
            notice = U.parse_notices(replaced, U.press_url('1267'))[0]
            self.assertEqual(notice['url'], 'https://main.un.org/securitycouncil/en/content/sc13673')


if __name__ == '__main__':
    unittest.main()
