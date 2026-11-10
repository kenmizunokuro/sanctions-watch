import copy
import gzip
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from src import integrity_audit as A, integrity_decisions as D, integrity_triage as T
from tests import test_integrity_audit as F
from tests.test_integrity_person_forms import document


class ReviewedStrongFormTests(unittest.TestCase):
    def case(self, *, weak=False, current=True, expires='2026-11-25T00:00:00Z', changed=False, approved=True):
        row=F.master(source='OFAC;財務省',category='財務省:タリバーン関係者等')
        mof_row=copy.deepcopy(row)
        if changed:mof_row['risk_level']='変更後'
        body=document(last='Doe',first='Jane',weak='true' if weak else 'false')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);relative='data/raw/ofac_sdn_advanced/current.xml.gz'
            path=root/relative;path.parent.mkdir(parents=True);path.write_bytes(gzip.compress(body,mtime=0))
            evidence_report=dict(documents=[dict(source='ofac_sdn',role='advanced',raw_path=relative,
                source_url='https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.XML',
                sha256=hashlib.sha256(body).hexdigest())])
            witnesses,_,_=T.collect_witnesses(root,evidence_report,{'janedoe'})
        proof=next(p for p in witnesses['janedoe'] if p['field']=='person_name_parts')
        proof['current']=current
        report=dict(status='BLOCKED',findings=[
            dict(kind='EXTRA_REGISTRATION',checked_source='OFAC',master=row,blocking=True),
            dict(kind='WEAK_ALIAS_ACTIVE',official=dict(F.expected(weak=True,primary=False),source='財務省',category='2'),master=mof_row,blocking=True)])
        triage=T.classify(report,[row],[],{'janedoe':[proof]})
        item=triage['items'][0]
        ledger=dict(schema_version=1,entries=[dict(review_id=item['review_id'],decision='APPROVED_RETAIN',
                    reviewed_at='2026-11-01T00:00:00Z',expires_at=expires,reviewed_by='Explicit test approval',
                    reason='Retain this exact reviewed typed form',reviewed_evidence_sha256=item['evidence_sha256'])] if approved else [])
        result=D.apply(report,triage,ledger,now=datetime(2026,11,10,tzinfo=timezone.utc))
        return A._finish(result)

    def test_exact_approved_current_strong_form_supports_mof_weak_name(self):
        result=self.case()
        self.assertTrue(result['publication_allowed'])
        self.assertEqual(result['findings'][1]['reviewed_strong_source'],'OFAC')

    def test_weak_or_historical_or_unapproved_form_cannot_release_mof_veto(self):
        for args in ({'weak':True},{'current':False},{'approved':False}):
            with self.subTest(args=args):
                self.assertTrue(self.case(**args)['findings'][1]['blocking'])

    def test_expired_or_different_master_approval_cannot_release_mof_veto(self):
        for args in ({'expires':'2026-11-09T00:00:00Z'},{'changed':True}):
            with self.subTest(args=args):
                self.assertTrue(self.case(**args)['findings'][1]['blocking'])


if __name__=='__main__':unittest.main()
