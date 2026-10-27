# 明示姓名フィールド監査 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Continue inline on the existing Phase 1 feature branch.

**Goal:** 原本の明示フィールドに基づいて姓名順の根拠を示し、由来未確認の誤分類を減らす。

**Architecture:** 既存取込・正規化・基本監査リーダーは変更しない。現行XMLを独立SAXで読み、triageの根拠として追加する。

**Tech Stack:** Python標準ライブラリ、XML SAX、既存unittest/bash。

**Spec:** `docs/superpowers/specs/2026-10-24-integrity-person-forms-design.md`

## Global Constraints

本番master、既存キー、会社Apps Script、保持承認は変更しない。自動削除・解除・Phase 2に着手しない。原本の部品を追加Aliasと混同しない。

## Review Focus

同名別Party、弱別名の昇格、script混合、不完全な部品宣言、パーサ変更を過去回収実験へ混入すること、解析失敗時の停止と証跡保存。

## Tasks

- [x] Aam/Abbasの原本と旧キーを確認し、物理順と姓名型の差を再現する。
- [x] 原本部品の保持・弱品質・Entity/異script除外・弱/強根拠衝突をテストで失敗再現する。
- [x] 独立SAXリーダーとtriage根拠分類を実装する。
- [x] 不完全/曖昧メタデータ、False Identity、別Partyと承認境界、実ゲート停止を追加検証する。
- [x] 過去回収の意味的baselineを固定して、将来の異なるパーサでの再実行を拒否する。
- [x] 実データ監査・全分類・未承認候補・資料不足一覧を別名保存し、独立レビュー、全552/552・offline213/213、実監査3回のJSON一致を確認する。
- [x] 共有masterの公開経路を実bashで検証し、財務省/OFACいずれの取得でも全必須ソースを監査する。独立レビューと両方向の公開停止回帰を完了する。
- [x] 同じPRへの反映を準備する。GitHub反映とチェックはPR本文・最終報告へ記録する。Draft/公開停止を維持する。

Ruling: 旧キー再採番ではなく、元フィールドに固定した監査用表示候補を採用する。共有正規化の修正は今回のスコープに含めず、残る運用上の制限として報告する。

Final review: 新たに消費するメタデータ構造の未知要素を部分的に読み飛ばせるP2を修正。未知ラッパー/参照型内要素での誤通過を実ゲートでRED→GREENとし、全552件通過。別原本による同一性確認、公式schemaの全将来形、法的な保持判断はこのレビューの判断外として報告する。
