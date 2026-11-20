from __future__ import annotations

import copy
import csv
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src import dashboard as D
from src import integrity_audit as IA
from src import master as M
from src import ofac_alias_removal_queue as OARQ
from src import ofac_removal_queue as ORQ


HASH_A = "a" * 64
HASH_B = "b" * 64


class OfacAliasRemovalQueueTests(unittest.TestCase):
    def _master_rows(self) -> dict[str, dict]:
        return {
            "ndranghetaorganization": {
                "match_key": "ndranghetaorganization",
                "display_name": "'NDRANGHETA ORGANIZATION",
                "status": "有効",
                "risk_type": "制裁リスト",
                "risk_level": "高",
                "first_seen_ms": "1000",
                "last_updated_ms": "1000",
                "sources": "OFAC",
                "categories": "OFAC:SDN",
                "remark": "制裁リスト（OFAC：SDN）",
                "invalid_reason": "",
                "review_flag": "",
                "variants": '["\'NDRANGHETA ORGANIZATION"]',
            },
            "ndrangheta": {
                "match_key": "ndrangheta",
                "display_name": "'NDRANGHETA",
                "status": "有効",
                "risk_type": "制裁リスト",
                "risk_level": "高",
                "first_seen_ms": "2000",
                "last_updated_ms": "2000",
                "sources": "OFAC",
                "categories": "OFAC:SDN",
                "remark": "制裁リスト（OFAC：SDN）",
                "invalid_reason": "",
                "review_flag": "",
                "variants": '["\'NDRANGHETA"]',
            },
            "abual-fadl": {
                "match_key": "abual-fadl",
                "display_name": "Abu al-Fadl",
                "status": "有効",
                "risk_type": "制裁リスト",
                "risk_level": "高",
                "first_seen_ms": "1000",
                "last_updated_ms": "1000",
                "sources": "OFAC",
                "categories": "",
                "remark": "制裁リスト（OFAC）",
                "invalid_reason": "",
                "review_flag": "",
                "variants": '["Abu al-Fadl"]',
            },
        }

    def _history_before_and_after(self) -> tuple[dict, dict]:
        prev = {
            ("SDN", "10902", "'NDRANGHETA ORGANIZATION"): {
                "list": "SDN",
                "party_id": "10902",
                "name": "'NDRANGHETA ORGANIZATION",
                "match_key": "ndranghetaorganization",
                "first_seen_ms": "1000",
                "last_changed_ms": "1000",
                "alias_current": "1",
                "party_current": "1",
                "formats": "advanced_xml_v3;classic_csv",
                "primary": "1",
                "low_quality": "0",
            },
        }
        curr = {
            ("SDN", "10902", "'NDRANGHETA ORGANIZATION"): {
                **prev[("SDN", "10902", "'NDRANGHETA ORGANIZATION")],
                "alias_current": "0",
                "party_current": "1",
                "last_changed_ms": "2000",
            },
            ("SDN", "10902", "'NDRANGHETA"): {
                "list": "SDN",
                "party_id": "10902",
                "name": "'NDRANGHETA",
                "match_key": "ndrangheta",
                "first_seen_ms": "2000",
                "last_changed_ms": "2000",
                "alias_current": "1",
                "party_current": "1",
                "formats": "advanced_xml_v3;classic_csv",
                "primary": "1",
                "low_quality": "0",
            },
        }
        return prev, curr

    def test_detects_active_party_alias_rename_and_stale_person_form(self):
        rows = self._master_rows()
        prev_hist, curr_hist = self._history_before_and_after()
        prev_forms = [
            {
                "source": "OFAC",
                "category": "SDN",
                "source_id": "27320",
                "name": "Abu al-Fadl",
                "primary": False,
                "weak": True,
            }
        ]
        cands = OARQ.detect_candidates(
            rows,
            prev_hist,
            curr_hist,
            previous_person_forms=prev_forms,
            current_person_forms=[],
        )
        self.assertEqual(
            {(c.list_name, c.party_id, c.match_key, c.relation_kind) for c in cands},
            {
                ("SDN", "10902", "ndranghetaorganization", "alias_removed"),
                ("SDN", "27320", "abual-fadl", "person_form_removed"),
            },
        )

        queue: list[dict] = []
        qdiff = OARQ.reconcile(
            queue,
            cands,
            current_strong_keys={"ndrangheta"},
            current_person_form_keys=set(),
            snapshot_hashes={"SDN": HASH_A, "Consolidated": HASH_B},
            ts=2000,
        )
        self.assertEqual(len(qdiff.created_events), 2)
        self.assertEqual(len(OARQ.pending_rows(queue)), 2)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            OARQ.save(queue, root / OARQ.QUEUE_REL)
            self.assertEqual(OARQ.load(root / OARQ.QUEUE_REL), queue)
            D.append_ofac_removal_reviews(root, [], queue)
            with (root / D.DASH / "changes.csv").open(encoding="utf-8") as f:
                changes = list(csv.DictReader(f))
            self.assertEqual(len(changes), 2)
            self.assertEqual(
                {c["受取人名"] for c in changes},
                {"'NDRANGHETA ORGANIZATION", "Abu al-Fadl"},
            )
            self.assertTrue(
                all(c["種別"] == "掲載終了候補（要確認）" for c in changes)
            )

            expected = [
                {
                    "source": "OFAC",
                    "category": "SDN",
                    "source_id": "10902",
                    "name": "'NDRANGHETA",
                    "primary": True,
                    "weak": False,
                }
            ]
            base_report = IA.compare(
                expected,
                list(rows.values()),
                list(curr_hist.values()),
                {"SDN": "OFAC:SDN", "Consolidated": "OFAC:Consolidated"},
            )
            self.assertFalse(base_report["publication_allowed"])
            relieved = OARQ.apply_audit_relief(
                root,
                base_report,
                expected_records=expected,
                index_rows=list(curr_hist.values()),
            )
            self.assertTrue(relieved["publication_allowed"])
            self.assertEqual(relieved["status"], "REVIEW")
            self.assertEqual(relieved["blocking_counts"], {})

    def test_reappearance_cancels_and_cdn_flap_does_not_collide_event_id(self):
        rows = self._master_rows()
        prev_hist, curr_hist = self._history_before_and_after()
        cands = OARQ.detect_candidates(rows, prev_hist, curr_hist)
        queue: list[dict] = []
        OARQ.reconcile(
            queue,
            cands,
            current_strong_keys={"ndrangheta"},
            snapshot_hashes={"SDN": HASH_A},
            ts=1000,
        )
        first_id = queue[0]["event_id"]

        # Reappears in HASH_B -> CANCELLED_REAPPEARED
        qdiff = OARQ.reconcile(
            queue,
            set(),
            current_strong_keys={"ndrangheta", "ndranghetaorganization"},
            snapshot_hashes={"SDN": HASH_B},
            ts=2000,
        )
        self.assertEqual(
            qdiff.cancelled_items,
            {("SDN", "10902", "ndranghetaorganization")},
        )
        self.assertEqual(OARQ.pending_rows(queue), [])

        # CDN flaps back to HASH_A -> creates salted event_id without error
        qdiff2 = OARQ.reconcile(
            queue,
            cands,
            current_strong_keys={"ndrangheta"},
            snapshot_hashes={"SDN": HASH_A},
            ts=3000,
        )
        self.assertEqual(len(qdiff2.created_events), 1)
        pending = OARQ.pending_rows(queue)
        self.assertEqual(len(pending), 1)
        self.assertNotEqual(pending[0]["event_id"], first_id)

        # Party removal queue also survives CDN flap A -> B -> A
        pqueue: list[dict] = []
        p_hist = {
            ("SDN", "100", "ALPHA"): {
                "list": "SDN",
                "party_id": "100",
                "name": "ALPHA",
                "match_key": "alpha",
                "party_current": "0",
                "alias_current": "0",
                "primary": "1",
                "low_quality": "0",
            }
        }
        ORQ.reconcile(pqueue, {("SDN", "100")}, set(), {"SDN": HASH_A}, p_hist, ts=1000)
        ORQ.reconcile(pqueue, set(), {("SDN", "100")}, {"SDN": HASH_B}, p_hist, ts=2000)
        ORQ.reconcile(pqueue, {("SDN", "100")}, set(), {"SDN": HASH_A}, p_hist, ts=3000)
        self.assertEqual(len(ORQ.pending_groups(pqueue)), 1)

    def test_delist_and_retain_approvals_apply_cleanly(self):
        rows = self._master_rows()
        prev_hist, curr_hist = self._history_before_and_after()
        prev_forms = [
            {
                "source": "OFAC",
                "category": "SDN",
                "source_id": "27320",
                "name": "Abu al-Fadl",
                "primary": False,
                "weak": True,
            }
        ]
        cands = OARQ.detect_candidates(
            rows,
            prev_hist,
            curr_hist,
            previous_person_forms=prev_forms,
            current_person_forms=[],
        )
        queue: list[dict] = []
        OARQ.reconcile(
            queue,
            cands,
            current_strong_keys={"ndrangheta"},
            snapshot_hashes={"SDN": HASH_A},
            ts=1000,
        )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            approvals_path = root / OARQ.APPROVALS_REL
            approvals_path.parent.mkdir(parents=True, exist_ok=True)
            with approvals_path.open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=OARQ.APPROVAL_FIELDS, lineterminator="\n")
                w.writeheader()
                w.writerow({
                    "list": "SDN",
                    "snapshot_sha256": HASH_A,
                    "party_id": "10902",
                    "match_key": "ndranghetaorganization",
                    "alias_name": "'NDRANGHETA ORGANIZATION",
                    "decision": "RETAIN",
                    "approved_by": "Compliance Reviewer",
                    "approved_at": "2026-11-20T04:00:00Z",
                    "official_url": "https://ofac.treasury.gov/recent-actions/20261119",
                    "notes": "Keep former organization name active for screening",
                })
                w.writerow({
                    "list": "SDN",
                    "snapshot_sha256": HASH_A,
                    "party_id": "27320",
                    "match_key": "abual-fadl",
                    "alias_name": "Abu al-Fadl",
                    "decision": "DELIST",
                    "approved_by": "Compliance Reviewer",
                    "approved_at": "2026-11-20T04:00:00Z",
                    "official_url": "https://ofac.treasury.gov/recent-actions/20261119",
                    "notes": "Delist obsolete person-order variant",
                })

            approvals = OARQ.load_available_approvals(approvals_path, queue)
            self.assertEqual(len(approvals), 2)

            before_sha = OARQ.OR.master_state_sha256(rows)
            idx_sha = OARQ.OR.index_state_sha256(curr_hist)
            diff, effects = OARQ.apply_to_master(
                rows,
                curr_hist,
                approvals,
                ts=2000,
            )
            self.assertEqual(len(diff.removed), 1)
            self.assertEqual(rows["abual-fadl"]["status"], M.STATUS_INACTIVE)
            self.assertEqual(rows["abual-fadl"]["invalid_reason"], M.DELISTED)
            self.assertEqual(rows["ndranghetaorganization"]["status"], M.STATUS_ACTIVE)

            OARQ.mark_applied(queue, approvals, ts=2000)
            self.assertEqual(OARQ.pending_rows(queue), [])
            OARQ.save(queue, root / OARQ.QUEUE_REL)

            audit_rows = OARQ.build_audit_rows(
                effects,
                master_before_sha256=before_sha,
                master_after_sha256=OARQ.OR.master_state_sha256(rows),
                index_before_sha256=idx_sha,
                index_after_sha256=idx_sha,
            )
            OARQ.append_audit(
                root / OARQ.AUDIT_REL,
                audit_rows,
                applied_at=datetime(2026, 11, 20, 4, 5, tzinfo=timezone.utc),
            )

            # Audit still allows publication for the APPROVED_RETAIN row
            expected = [
                {
                    "source": "OFAC",
                    "category": "SDN",
                    "source_id": "10902",
                    "name": "'NDRANGHETA",
                    "primary": True,
                    "weak": False,
                }
            ]
            base_report = IA.compare(
                expected,
                list(rows.values()),
                list(curr_hist.values()),
                {"SDN": "OFAC:SDN", "Consolidated": "OFAC:Consolidated"},
            )
            relieved = OARQ.apply_audit_relief(
                root,
                base_report,
                expected_records=expected,
                index_rows=list(curr_hist.values()),
            )
            self.assertTrue(relieved["publication_allowed"])
            self.assertEqual(relieved["blocking_counts"], {})


if __name__ == "__main__":
    unittest.main()
