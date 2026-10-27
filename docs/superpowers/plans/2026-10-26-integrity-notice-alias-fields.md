# 明示別名欄監査 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Continue inline on isolated worktree, update existing Phase 1 PR.

**Goal:** 姓名順が違う未確認名称に、公式発表の明示別名フィールドを根拠として添える。

**Architecture:** 既存HTML本文レコード読取から、完全に区切られた別名グループを独立解釈する。既存match_keyで候補を結び、NOTICE_ALIAS_FIELD_REVIEW（保持対象外）へ分類。元データ・公開基準は不変。

**Tech Stack:** Python標準ライブラリ/unittest、既存HTMLParser/triage/bash。

**Spec:** `docs/superpowers/specs/2026-10-26-integrity-notice-alias-fields-design.md`

## Global Constraints

current=false、Party ID/Weak品質不明、保持対象外。master/state/raw/Alias履歴・Apps Script不変、保持承認0、削除/解除/Phase2/mergeなし。旧証跡は別名保存。

## Review Focus

法人名内の括弧/引用符/セミコロン、Related To内の言及、明示されない品質の昇格、同名別レコードの混同、未知別名マーカーからの部分採用。各入力をTask1の実動作テストで検証する。

## Task 1: Reader and gate integration

Files: `src/integrity_notice_aliases.py`, `src/integrity_notice_context.py`, `src/integrity_triage.py`, `tests/test_integrity_notice_aliases.py`。
Interfaces: `read_records(body: bytes) -> list[dict]`; `read_notice_aliases(body: bytes) -> list[dict]` は元レコード・マーカー・spanを保つ。既存add_notice_witnessesが原本hashを検証してから読み込む。

- [x] 括弧/引用符/区切り、文字位置、Related To、姓名順、保持対象外、未知形式での実bash停止をREDで確認。
- [x] 最小実装後、対象テスト・既存全テスト成功を確認。

## Task 2: Corpus, evidence and PR

Consumes Task1のリーダー・triage分類。

- [x] 全原本で解析・照合し、前回からの分類/ID差分と原文対応を保存。全候補PENDING。
- [x] 監査/triageを2回実行しJSON一致、全テスト/offline、旧証跡・業務データ不変を確認。
- [x] 独立レビュー、必要修正のRED→GREEN、全検証、資料、既存PR更新とCI確認を完了。Draft/BLOCKED維持。

Ruling: Phase1の継続指示に従い、フィールド根拠を追加する。姓名キーの一致は同一性確認ではなく、保持可能性は広げない。誤っていれば人の根拠レビューで修正が必要になるため、元レコードと停止条件を保つ。

Final review: Important3件を実bashゲートでRED→GREEN。閉じた関係欄の影響が変更後側へ誤伝播する問題もRED→GREEN。最終577/577・offline213/213、最終監査2回JSON一致。既存のBUGAYENKO説明の誤りを訂正し、原本/既存JSONは不変。PR反映・CIはPR本文へ記録。
