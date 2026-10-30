"""MOF refresh must not strip explicit, expiring legacy review evidence."""
import copy
import unittest

from src import master as M

WARNING = (
    "旧登録・一次出典未確認。旧登録の照合文字列として期限付き保持。"
    "現行公式掲載・本人同一性・当初取込意図を確認済みと扱わない。"
    "保持期限UTC: 2026-11-25T08:21:15Z"
)


def approved_row():
    return dict(
        match_key="examplealias",
        display_name="EXAMPLE ALIAS",
        status=M.STATUS_ACTIVE,
        risk_type="旧登録・一次出典未確認／制裁リスト",
        risk_level="高",
        first_seen_ms="1000",
        last_updated_ms="1000",
        sources="OFAC;財務省",
        categories="財務省:タリバーン関係者等",
        remark="制裁リスト（財務省：タリバーン関係者等／OFAC）\n" + WARNING,
        invalid_reason="",
        review_flag="LEGACY_UNVERIFIED_V1:ORIGIN_UNRESOLVED",
        variants='["EXAMPLE ALIAS"]',
    )


class MofLegacyAnnotationTests(unittest.TestCase):
    def test_unchanged_source_does_not_strip_warning_or_touch_timestamp(self):
        row = approved_row()
        original = copy.deepcopy(row)
        master = {row["match_key"]: row}
        diff = M.merge(master, [
            {"name": row["display_name"], "category": "タリバーン関係者等"},
        ], "財務省", ts=2000)
        self.assertEqual(master[row["match_key"]], original)
        self.assertEqual(diff.counts, {"追加": 0, "削除": 0, "変更": 0})

    def test_mof_source_removed_but_ofac_kept_preserves_warning_for_review(self):
        row = approved_row()
        master = {row["match_key"]: row}
        diff = M.merge(master, [], "財務省", ts=2000)
        updated = master[row["match_key"]]
        self.assertEqual(updated["sources"], "OFAC")
        self.assertEqual(updated["categories"], "")
        self.assertEqual(updated["status"], M.STATUS_ACTIVE)
        self.assertEqual(updated["last_updated_ms"], "2000")
        self.assertEqual(updated["review_flag"], row["review_flag"])
        self.assertEqual(updated["remark"], M._recompute(dict(updated))["remark"])
        self.assertTrue(updated["remark"].endswith("\n" + WARNING))
        self.assertEqual(len(diff.removed), 1)
        # The immutable acceptance ledger must still review this source
        # relation change; preserving the annotation is not an approval.
        self.assertNotEqual(updated, approved_row())

    def test_wrong_or_missing_warning_fails_closed(self):
        for remark in ("制裁リスト（OFAC）", "制裁リスト（OFAC）\n警告なし"):
            with self.subTest(remark=remark):
                row = approved_row()
                row["remark"] = remark
                with self.assertRaisesRegex(ValueError, "legacy retention annotation"):
                    M.merge({row["match_key"]: row}, [
                        {"name": row["display_name"], "category": "タリバーン関係者等"}
                    ], "財務省", ts=2000)

    def test_normal_row_still_rebuilds_source_summary(self):
        row = approved_row()
        row["review_flag"] = ""
        row["remark"] = "not a retention warning"
        M._recompute(row)
        self.assertNotIn("保持期限UTC:", row["remark"])
        self.assertIn("財務省", row["remark"])


if __name__ == "__main__":
    unittest.main()
