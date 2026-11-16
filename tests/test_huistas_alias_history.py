import copy
import csv
import gzip
import hashlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src import integrity_decisions as decisions
from src import integrity_triage as triage
from src import master
from src import ofac_alias_history as history


ROOT = Path(__file__).resolve().parent.parent
APPROVAL = 'data/review/approved_huistas_alias_history_20261116.json'
NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)
KEY = 'loshuistasdto'
PRIMARY = 'LOS HUISTAS DRUG TRAFFICKING ORGANIZATION'


class HuistasArchivedEvidenceTest(unittest.TestCase):
    def test_sealed_approval_reads_the_real_historical_weak_alias(self):
        bundle = history.approval(ROOT, huistas=True)
        facts = history._huistas_evidence(ROOT, bundle)
        self.assertEqual(facts['party_id'], '32452')
        self.assertEqual(facts['name_status'], 'historical_weak_alias')


class HuistasHistoryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.bundle = json.loads((ROOT / APPROVAL).read_bytes())
        historical = self.xml(include_weak=True)
        self.bundle['historical']['source_hash'] = hashlib.sha256(historical).hexdigest()
        self.write(self.bundle['historical']['raw_path'], gzip.compress(historical))
        body = json.dumps(self.bundle).encode()
        self.write(APPROVAL, body)
        self.enterContext(patch.object(history, 'HUISTAS_APPROVAL_SHA256', hashlib.sha256(body).hexdigest(), create=True))
        self.write(history.LEDGER, b'{"schema_version":1,"entries":[]}')
        self.write('data/diff/latest.csv', '出所,種別,受取人名,変更前,変更後\n'.encode())
        self.other = dict(self.bundle['before'], match_key='other', display_name='OTHER', sources='UK')
        master.save({KEY: self.bundle['before'], 'other': self.other}, self.root / 'data/master/master.csv')
        self.current()

    def write(self, rel, body):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    def xml(self, *, include_weak=False, party='32452'):
        weak = ('<Alias Primary="false" LowQuality="true"><DocumentedName ID="51896">'
                '<DocumentedNamePart><NamePartValue ScriptID="215">Los Huistas DTO</NamePartValue>'
                '</DocumentedNamePart></DocumentedName></Alias>') if include_weak else ''
        return (
            '<Sanctions xmlns="https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ADVANCED_XML" Version="3">'
            f'<DistinctParties><DistinctParty FixedRef="{party}"><Profile><Identity ID="1" False="false">'
            f'<Alias Primary="true" LowQuality="false"><DocumentedName ID="1"><DocumentedNamePart>'
            f'<NamePartValue ScriptID="215">{PRIMARY}</NamePartValue></DocumentedNamePart>'
            f'</DocumentedName></Alias>{weak}</Identity></Profile></DistinctParty></DistinctParties></Sanctions>'
        ).encode()

    def current(self, *, include_weak=False, party='32452'):
        state = {}
        self.docs = []
        rows = io.StringIO(newline='')
        csv.writer(rows).writerow(['32452', PRIMARY, '-0-', 'SDGT] [ILLICIT-DRUGS-EO14059', *(['-0-'] * 7), 'Target Type Criminal Organization'])
        for role, content, state_fields in (
            ('primary', rows.getvalue().encode(), ('raw_prim', 'sha256', 'url')),
            ('alias', b'30000,10,aka,Other alias,-0-\n', ('raw_alt', 'alt_sha256', 'alt_url')),
            ('advanced', self.xml(include_weak=include_weak, party=party), ('raw_advanced', 'advanced_sha256', 'advanced_url')),
        ):
            folder = 'ofac_sdn_advanced' if role == 'advanced' else 'ofac_sdn'
            rel = f'data/raw/{folder}/test-{role}'
            self.write(rel, content)
            doc = dict(source='ofac_sdn', role=role, raw_path=rel,
                       sha256=hashlib.sha256(content).hexdigest(), source_url=history.URLS[role])
            self.docs.append(doc)
            state.update(zip(state_fields, (rel, doc['sha256'], doc['source_url'])))
        self.write('data/state.json', json.dumps({'ofac_sdn': state}).encode())

    def test_application_retains_manual_history_and_excludes_single_name_screening(self):
        receipt = history.apply(self.root, now=NOW, huistas=True)
        rows = master.load(self.root / 'data/master/master.csv')
        self.assertEqual(rows[KEY], self.bundle['after'])
        self.assertEqual(rows['other'], self.other)
        self.assertEqual(rows[KEY]['first_seen_ms'], self.bundle['before']['first_seen_ms'])
        self.assertEqual(rows[KEY]['status'], '無効')
        display = (self.root / 'data/dashboard/list.csv').read_text()
        self.assertIn('Los Huistas DTO,制裁リスト（OFAC旧Weak Alias）,無効', display)
        self.assertNotIn('Los Huistas DTO', gzip.decompress(
            (self.root / 'data/dashboard/screening.csv.gz').read_bytes()).decode())
        self.assertEqual(receipt['count'], 1)
        self.assertEqual(history.apply(self.root, now=NOW, huistas=True)['status'], 'ALREADY_APPLIED')

    def test_when_fetch_has_no_diff_previous_csv_is_not_republished(self):
        self.write('data/diff/latest.csv', '出所,種別,受取人名,変更前,変更後\nUK,追加,OTHER,,old\n'.encode())
        history.apply(self.root, now=NOW, huistas=True, fresh_diff=False)
        changes = list(csv.DictReader(io.StringIO((self.root / 'data/diff/latest.csv').read_text())))
        self.assertEqual([row['受取人名'] for row in changes], ['Los Huistas DTO'])

    def test_changed_live_party_or_reappearing_alias_blocks_before_any_writes(self):
        for kwargs in ({'party': '55555'}, {'include_weak': True}):
            with self.subTest(kwargs=kwargs):
                self.current(**kwargs)
                snapshot = {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
                with self.assertRaisesRegex(ValueError, 'primary name missing|alias is current'):
                    history.apply(self.root, now=NOW, huistas=True)
                self.assertEqual(snapshot, {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()})

    def test_only_exact_historical_finding_is_released(self):
        history.apply(self.root, now=NOW, huistas=True)
        b = self.bundle
        finding = {'kind': 'EXTRA_REGISTRATION', 'checked_source': 'OFAC', 'blocking': True, 'master': b['after']}
        unrelated = {'kind': 'UPSTREAM_FAILED', 'blocking': True}
        report = dict(status='BLOCKED', scope=['ofac'], documents=self.docs, findings=[finding, unrelated])
        investigation = triage.classify(report, [b['after']], b['reviewed_index_links'], {KEY: b['reviewed_evidence']})
        ledger = json.loads((self.root / history.LEDGER).read_bytes())
        result = decisions.apply(report, investigation, ledger, root=self.root, now=NOW)
        self.assertEqual([item['blocking'] for item in result['findings']], [False, True])
        self.assertEqual(result['retention_review'][0]['alias_history']['party_id'], '32452')
        tampered = copy.deepcopy(ledger)
        tampered['entries'][0]['alias_history']['party_id'] = '12345'
        result = decisions.apply(report, investigation, tampered, root=self.root, now=NOW)
        self.assertTrue(result['findings'][0]['blocking'])
