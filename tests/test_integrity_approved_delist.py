"""The fixed historical MOF exception must never authorize a current listing."""
import copy
import csv
import gzip
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from src import approved_legacy_delist as D, integrity_audit as A, integrity_readers as R
from src import integrity_approved_delist as P
from src.normalize import match_key

ROOT = Path(__file__).resolve().parent.parent
RAW = 'data/raw/mof/20261020T120119Z__shisantouketsu20261021.csv.gz'
URL = 'https://www.mof.go.jp/policy/international_policy/gaitame_kawase/gaitame/economic_sanctions/shisantouketsu20261021.csv'
OLD_HASH = 'f8e1038419ab0146cbce89ae9fa7b5f9d298548c8bca4953f970c01f87b3f7bc'


class ApprovedDelistAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        body = gzip.decompress((ROOT / RAW).read_bytes())
        cls.document = dict(source='mof', role='mof', sha256=OLD_HASH, source_url=URL,
                            raw_path=RAW, bytes=len(body), name_count=15693)
        cls.records = {match_key(r['name']): dict(r, source_url=URL, source_hash=OLD_HASH, raw_path=RAW)
                       for r in R.read_mof(body) if match_key(r['name']) in D.KEYS}

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for rel in (D.APPROVAL, D.RECEIPT):
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / rel, target)
        self.approval = json.loads((self.root / D.APPROVAL).read_bytes())
        self.rows = [copy.deepcopy(c['after']) for c in self.approval['cases']]
        self.rows.append(dict(self.rows[0], match_key='unrelated', display_name='Unrelated'))
        self.report = dict(schema_version=1, scope=['mof', 'ofac'], documents=[copy.deepcopy(self.document)],
                           inputs={}, findings=[], lineage=[{'preserved': True}], regression=[])
        self.save_master()
        for case in self.approval['cases']:
            for kind in ('STATUS_MISMATCH', 'SOURCE_MISMATCH'):
                self.report['findings'].append(dict(kind=kind, detail='original detail', blocking=True,
                    official=copy.deepcopy(self.records[case['match_key']]), master=copy.deepcopy(case['after'])))
        A._finish(self.report)

    def save_master(self):
        path = self.root / 'data/master/master.csv'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(self.rows[0]))
            writer.writeheader(); writer.writerows(self.rows)
        self.report['inputs']['data/master/master.csv'] = hashlib.sha256(path.read_bytes()).hexdigest()

    def test_six_exact_historical_pairs_are_visible_review_only_and_inputs_are_bound(self):
        before = copy.deepcopy(self.report)
        files = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = P.apply(self.root, self.report)
        self.assertEqual(files, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.assertEqual(self.report, before)
        self.assertEqual(result['status'], 'REVIEW')
        self.assertTrue(result['publication_allowed'])
        self.assertEqual(result['counts'], {'SOURCE_MISMATCH': 6, 'STATUS_MISMATCH': 6})
        self.assertEqual(result['blocking_counts'], {})
        self.assertEqual(result['lineage'], before['lineage'])
        for original, finding in zip(before['findings'], result['findings']):
            self.assertFalse(finding['blocking'])
            self.assertEqual(finding['official'], original['official'])
            self.assertEqual(finding['master'], original['master'])
            self.assertEqual(finding['detail'], original['detail'])
            self.assertEqual(finding['approved_delist']['classification'], 'APPROVED_DELIST_HISTORICAL_SOURCE')
            self.assertEqual(finding['approved_delist']['historical_source_sha256'], OLD_HASH)
        for rel in (D.APPROVAL, D.RECEIPT):
            self.assertEqual(result['inputs'][str(rel)], hashlib.sha256((self.root / rel).read_bytes()).hexdigest())
        self.assertEqual(P.apply(self.root, result), result)

    def test_missing_receipt_preserves_original_blockers(self):
        (self.root / D.RECEIPT).unlink()
        self.assertEqual(P.apply(self.root, self.report), self.report)

    def test_tampered_approval_fails_closed(self):
        path = self.root / D.APPROVAL
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaises(ValueError): P.apply(self.root, self.report)

    def test_malformed_or_unbound_receipt_fails_closed(self):
        path = self.root / D.RECEIPT
        receipt = json.loads(path.read_bytes())
        variants = [[], {}, dict(receipt, status='DRAFT'), dict(receipt, count=5),
                    dict(receipt, count=True), dict(receipt, approval_sha256='f' * 64),
                    dict(receipt, approved_at='2026-11-07T00:00:00Z'),
                    dict(receipt, applied_at='not a date'),
                    dict(receipt, applied_at='2026-11-08T00:00:00Z'),
                    dict(receipt, outputs={})]
        for variant in variants:
            with self.subTest(variant=variant):
                path.write_text(json.dumps(variant))
                with self.assertRaises(ValueError): P.apply(self.root, self.report)
        path.write_text('{')
        with self.assertRaises(ValueError): P.apply(self.root, self.report)

    def test_receipt_global_master_hash_does_not_pin_later_unrelated_updates(self):
        self.rows[-1]['remark'] = 'a legitimate unrelated update'
        self.save_master()
        self.assertTrue(P.apply(self.root, self.report)['publication_allowed'])

    def test_changed_case_rows_are_not_released_individually(self):
        for field, value in [('status', '有効'), ('remark', 'changed'), ('sources', '財務省'),
                             ('variants', '["other"]'), ('review_flag', '')]:
            with self.subTest(field=field):
                report = copy.deepcopy(self.report)
                report['findings'][0]['master'][field] = value
                result = P.apply(self.root, report)
                self.assertTrue(result['findings'][0]['blocking'])
                self.assertNotIn('approved_delist', result['findings'][0])
                self.assertTrue(result['findings'][1].get('approved_delist'))

    def test_actual_changed_or_duplicated_master_case_remains_blocking(self):
        self.rows[0]['remark'] = 'changed actual row'
        self.save_master()
        result = P.apply(self.root, self.report)
        self.assertTrue(all(f['blocking'] for f in result['findings'][:2]))
        self.assertTrue(all(not f['blocking'] for f in result['findings'][2:]))
        self.rows[0] = copy.deepcopy(self.approval['cases'][0]['after'])
        self.rows.append(copy.deepcopy(self.rows[0]))
        self.save_master()
        result = P.apply(self.root, self.report)
        self.assertTrue(all(f['blocking'] for f in result['findings'][:2]))

    def test_master_report_input_mismatch_fails_closed(self):
        self.report['inputs']['data/master/master.csv'] = 'f' * 64
        with self.assertRaises(ValueError): P.apply(self.root, self.report)

    def test_new_or_unknown_document_never_uses_historical_authority(self):
        for changes in ({'sha256': 'f' * 64}, {'source_url': URL.replace('20261021', '20261109')},
                        {'source': 'ofac_sdn'}, {'role': 'primary'}, {'name_count': 0}, {'bytes': 0}):
            with self.subTest(changes=changes):
                report = copy.deepcopy(self.report)
                report['documents'][0].update(changes)
                self.assertEqual(P.apply(self.root, report)['findings'], report['findings'])
        for docs in ([], self.report['documents'] * 2):
            report = dict(self.report, documents=docs)
            self.assertEqual(P.apply(self.root, report)['findings'], report['findings'])

    def test_official_witness_tampering_new_listing_ofac_and_other_kind_stay_blocked(self):
        for target, changes in [('official', {'source': 'OFAC'}), ('official', {'source_hash': 'f' * 64}),
                                ('official', {'source_id': 'another-party'}), ('official', {'raw_path': 'other.csv'}),
                                ('official', {'name': 'ABU AHMAD AKHLAQ'}), ('official', {'weak': True}),
                                ('official', {'raw_record': {'番号': '002-999999'}}),
                                ('official', {'locator': 'line:999/別名・別称（英語）'}),
                                ('master', {'match_key': 'unrelated'})]:
            with self.subTest(changes=changes):
                report = copy.deepcopy(self.report)
                report['findings'][0][target].update(changes)
                result = P.apply(self.root, report)
                self.assertTrue(result['findings'][0]['blocking'])
                self.assertNotIn('approved_delist', result['findings'][0])
        report = copy.deepcopy(self.report)
        report['findings'][0]['kind'] = 'EXTRA_REGISTRATION'
        self.assertEqual(P.apply(self.root, report)['findings'][0], report['findings'][0])

    def test_other_blockers_upstream_and_schema_failures_survive(self):
        others = [dict(kind='UPSTREAM_FAILED', detail='fetch failed'),
                  dict(kind='UNDETERMINED', source='mof', detail='schema changed'),
                  dict(kind='DUPLICATE_RECORD', detail='duplicate'),
                  dict(kind='SOURCE_MISMATCH', detail='unrelated', master=self.rows[-1])]
        self.report['findings'].extend(others)
        result = P.apply(self.root, self.report)
        self.assertEqual(result['findings'][-4:], others)
        self.assertFalse(result['publication_allowed'])
        self.assertEqual(result['status'], 'BLOCKED')


if __name__ == '__main__': unittest.main()
