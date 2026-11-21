"""Regression tests for the 2026-11-21 -> 2026-12-21 30-day integrity renewal."""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src import approved_vessel_history as AV
from src import integrity_decisions as D
from src import integrity_legacy as L
from src import integrity_renewal as IR
from src import integrity_triage as T
from src import master as M
from src import ofac_alias_history as H
from src import vessel_history as V

ROOT = Path(__file__).resolve().parent.parent
IN_WINDOW = datetime(2026, 12, 5, 0, 0, 0, tzinfo=timezone.utc)
AFTER_EXPIRY = datetime(2026, 12, 22, 0, 0, 0, tzinfo=timezone.utc)


class IntegrityRenewalTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.renewal = IR.load_renewal(ROOT)
        self.assertIsNotNone(self.renewal)
        self.vessel_bundle = V.approval(ROOT)
        self.volta_bundle = H.approval(ROOT, huistas=False)
        self.huistas_bundle = H.approval(ROOT, huistas=True)

        files = [
            IR.RENEWAL,
            V.APPROVAL,
            AV.RECEIPT,
            H.APPROVAL,
            H.RECEIPT,
            H.HUISTAS_APPROVAL,
            H.HUISTAS_RECEIPT,
            L.POLICY_PATH,
            L.SCOPE_PATH,
            L.LEDGER_PATH,
            'data/review/integrity_retention_decisions.json',
            'data/master/master.csv',
            self.vessel_bundle['notice']['raw_path'],
            *[d['raw_path'] for d in self.vessel_bundle['historical'].values()],
            self.volta_bundle['notice']['raw_path'],
            self.volta_bundle['historical']['raw_path'],
            self.huistas_bundle['historical']['raw_path'],
        ]
        for rel in files:
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / rel, p)

        # Synthetic live OFAC docs proving current vessel/Volta/Huistas primary names
        self.docs = []
        prim_buf = io.StringIO(newline='')
        pw = csv.writer(prim_buf)
        for rec in self.vessel_bundle['current_verification']['records']:
            pw.writerow(rec)
        pw.writerow(self.volta_bundle['current_verification']['primary_record'])
        pw.writerow([
            '32452', self.huistas_bundle['primary_name'], '-0-',
            'SDGT] [ILLICIT-DRUGS-EO14059', *(['-0-'] * 7),
            'Target Type Criminal Organization',
        ])
        alt_buf = io.StringIO(newline='')
        csv.writer(alt_buf).writerow(['58138', '999', 'a.k.a.', 'VOLTA CONTAINER LINE FZCO', '-0-'])
        ns = 'https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ADVANCED_XML'
        sdn_xml = (
            f'<Sanctions xmlns="{ns}" Version="3"><DistinctParties>'
            '<DistinctParty FixedRef="58138"><Profile><Identity ID="1" False="false">'
            '<Alias Primary="true" LowQuality="false"><DocumentedName ID="1"><DocumentedNamePart>'
            '<NamePartValue ScriptID="1">VOLTA SHIPPING SERVICES LLC</NamePartValue>'
            '</DocumentedNamePart></DocumentedName></Alias>'
            '<Alias Primary="false" LowQuality="false"><DocumentedName ID="2"><DocumentedNamePart>'
            '<NamePartValue ScriptID="1">VOLTA CONTAINER LINE FZCO</NamePartValue>'
            '</DocumentedNamePart></DocumentedName></Alias>'
            '</Identity></Profile></DistinctParty>'
            '<DistinctParty FixedRef="32452"><Profile><Identity ID="2" False="false">'
            '<Alias Primary="true" LowQuality="false"><DocumentedName ID="3"><DocumentedNamePart>'
            f'<NamePartValue ScriptID="215">{self.huistas_bundle["primary_name"]}</NamePartValue>'
            '</DocumentedNamePart></DocumentedName></Alias>'
            '</Identity></Profile></DistinctParty>'
            '</DistinctParties></Sanctions>'
        ).encode()
        cons_xml = (
            f'<Sanctions xmlns="{ns}" Version="3"><DistinctParties>'
            '<DistinctParty FixedRef="55555"><Profile><Identity ID="1" False="false">'
            '<Alias Primary="true" LowQuality="false"><DocumentedName ID="1"><DocumentedNamePart>'
            '<NamePartValue ScriptID="215">OTHER PARTY</NamePartValue>'
            '</DocumentedNamePart></DocumentedName></Alias>'
            '</Identity></Profile></DistinctParty></DistinctParties></Sanctions>'
        ).encode()

        for source, urls, payloads in (
            ('ofac_sdn', H.URLS, {
                'primary': prim_buf.getvalue().encode(),
                'alias': alt_buf.getvalue().encode(),
                'advanced': sdn_xml,
            }),
            ('ofac_cons', H.CONS_URLS, {
                'primary': b'55555,OTHER PARTY,-0-,TEST,-0-,-0-,-0-,-0-,-0-,-0-,-0-,-0-\n',
                'alias': b'55555,10,aka,Other alias,-0-\n',
                'advanced': cons_xml,
            }),
        ):
            for role, body in payloads.items():
                folder = f'data/raw/{source}' + ('_advanced' if role == 'advanced' else '')
                rel = f'{folder}/live-{role}'
                p = self.root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(body)
                self.docs.append({
                    'source': source,
                    'role': role,
                    'source_url': urls[role],
                    'raw_path': rel,
                    'sha256': hashlib.sha256(body).hexdigest(),
                })

    def test_renewal_bundle_passes_in_december_and_original_bundles_stay_immutable(self):
        self.assertEqual(self.renewal['reviewed_at'], '2026-11-21T13:00:00Z')
        self.assertEqual(self.renewal['expires_at'], '2026-12-21T13:00:00Z')
        # Immutable historical approvals & receipts validate cleanly
        self.assertEqual(AV.apply(self.root, now=IN_WINDOW)['status'], 'ALREADY_APPLIED')
        self.assertEqual(H.apply(self.root, now=IN_WINDOW, huistas=False)['status'], 'ALREADY_APPLIED')
        self.assertEqual(H.apply(self.root, now=IN_WINDOW, huistas=True)['status'], 'ALREADY_APPLIED')

        # Legacy 2,306 rows validate in December and fail closed after Dec 21
        rows = list(M.load(self.root / 'data/master/master.csv').values())
        L.validate_for_export(self.root, rows, now=IN_WINDOW)
        with self.assertRaisesRegex(ValueError, 'expired'):
            L.validate_for_export(self.root, rows, now=AFTER_EXPIRY)

    def test_vessel_and_alias_decisions_validate_in_december_and_fail_after_expiry(self):
        ledger = json.loads((self.root / 'data/review/integrity_retention_decisions.json').read_bytes())
        by_id = {e['review_id']: e for e in ledger['entries']}

        # All 3 vessels verify with live SDN doc
        report = {'status': 'BLOCKED', 'scope': ['ofac'], 'documents': self.docs, 'findings': []}
        for c in self.vessel_bundle['cases']:
            dec = by_id[c['retention_decision']['review_id']]
            self.assertEqual(dec['expires_at'], '2026-12-21T13:00:00Z')
            facts = V.verify_decision(self.root, report, dec)
            self.assertEqual(facts['imo'], c['imo'])

        # Volta & Huistas validate annotations + live docs in December, fail after expiry
        clean_report = {'status': 'PASS', 'scope': ['ofac'], 'documents': self.docs, 'findings': [], 'publication_allowed': True}
        ok = H.validate_annotations(self.root, copy.deepcopy(clean_report), ledger, now=IN_WINDOW)
        self.assertTrue(ok['publication_allowed'], ok['findings'])

        expired = H.validate_annotations(self.root, copy.deepcopy(clean_report), ledger, now=AFTER_EXPIRY)
        self.assertFalse(expired['publication_allowed'])
        self.assertEqual(expired['findings'][0]['kind'], 'ALIAS_HISTORY_EVIDENCE_INVALID')

        # Standard entries expire after Dec 21 too
        standard_expiries = {e['expires_at'] for e in ledger['entries']}
        self.assertEqual(standard_expiries, {'2026-12-21T13:00:00Z'})

    def test_tampered_or_missing_renewal_bundle_fails_closed(self):
        ledger = json.loads((self.root / 'data/review/integrity_retention_decisions.json').read_bytes())
        clean_report = {'status': 'PASS', 'scope': ['ofac'], 'documents': self.docs, 'findings': [], 'publication_allowed': True}

        # Tamper 1 byte -> hash mismatch blocks
        p = self.root / IR.RENEWAL
        orig = p.read_bytes()
        p.write_bytes(orig + b' ')
        tampered = H.validate_annotations(self.root, copy.deepcopy(clean_report), ledger, now=IN_WINDOW)
        self.assertFalse(tampered['publication_allowed'])
        with self.assertRaisesRegex(ValueError, 'hash changed'):
            H.apply(self.root, now=IN_WINDOW, huistas=True)

        # Remove renewal file -> falls back to old expired/original bundles and blocks
        p.unlink()
        missing = H.validate_annotations(self.root, copy.deepcopy(clean_report), ledger, now=IN_WINDOW)
        self.assertFalse(missing['publication_allowed'])
        with self.assertRaisesRegex(ValueError, 'changed or incomplete'):
            H.apply(self.root, now=IN_WINDOW, huistas=True)


if __name__ == '__main__':
    unittest.main()
