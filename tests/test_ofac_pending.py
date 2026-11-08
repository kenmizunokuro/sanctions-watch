import hashlib
import unittest

from src import ofac_pending as pending
from src.ofac_pending import build, HEADER


class OfacPendingTest(unittest.TestCase):
    def test_candidate_is_separate_and_only_contains_ofac_rows(self):
        body = ('出所,種別,受取人名,変更前,変更後\n'
                'OFAC,追加,ALPHA,,OFAC:SDN\n財務省,変更,BETA,old,new\n'
                'OFAC,掲載終了,GAMMA,OFAC:SDN,\n').encode()
        rows = build(body, hashlib.sha256(body).hexdigest(), '2026-11-07T03:00:00Z',
                     'https://github.com/test/repo/actions/runs/123')
        self.assertEqual(rows[0], HEADER)
        self.assertEqual([row[3] for row in rows[1:]], ['ALPHA', 'GAMMA'])
        self.assertEqual(rows[2][2], '掲載終了候補')
        self.assertTrue(all(row[6] == '監査停止・master未反映' for row in rows[1:]))

    def test_hash_mismatch_and_structural_errors_are_rejected(self):
        body = '出所,種別,受取人名,変更前,変更後\nOFAC,変更,ALPHA,old\n'.encode()
        for digest in ('0' * 64, hashlib.sha256(body).hexdigest()):
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                build(body, digest, '2026-11-07T03:00:00Z', 'https://github.com/test/repo/actions/runs/123')

    def test_repeat_check_keeps_first_detection_and_its_run_url(self):
        old = [HEADER, ['2026-11-07 12:00:00','OFAC','追加','ALPHA','','OFAC:SDN',pending.STATE,
                        'https://github.com/test/repo/actions/runs/123']]
        current = [HEADER, ['2026-11-09 12:00:00', *old[1][1:7],
                            'https://github.com/test/repo/actions/runs/456']]
        self.assertEqual(pending.retain_first_detection(current, old), old)
        self.assertEqual(current[1][0], '2026-11-09 12:00:00')

    def test_changed_diff_and_reappearance_after_clear_get_new_detection(self):
        old = [HEADER, ['2026-11-07 12:00:00','OFAC','変更','ALPHA','old','one',pending.STATE,
                        'https://github.com/test/repo/actions/runs/123']]
        current = [HEADER, ['2026-11-09 12:00:00','OFAC','変更','ALPHA','old','two',pending.STATE,
                            'https://github.com/test/repo/actions/runs/456']]
        self.assertEqual(pending.retain_first_detection(current, old), current)
        self.assertEqual(pending.retain_first_detection(current, [HEADER]), current)
        self.assertEqual(pending.retain_first_detection([HEADER], old), [HEADER])

    def test_invalid_prior_evidence_is_rejected_instead_of_resetting_dates(self):
        row = ['2026-11-07 12:00:00','OFAC','追加','ALPHA','','OFAC:SDN',pending.STATE,
               'https://github.com/test/repo/actions/runs/123']
        current = [HEADER, ['2026-11-09 12:00:00', *row[1:]]]
        for before in ([HEADER, row, row], [HEADER, ['2026-11-10 12:00:00', *row[1:]]],
                       [HEADER, ['invalid', *row[1:]]], [HEADER, [*row[:7], 'invalid-url']],
                       [['old schema'], row]):
            with self.subTest(before=before), self.assertRaises(ValueError):
                pending.retain_first_detection(current, before)
