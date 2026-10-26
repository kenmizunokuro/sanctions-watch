# 公式発表の文脈追跡 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Continue inline on the existing authorized Phase 1 branch/PR.

**Goal:** 出典未確認147名称を追加の公式発表へ結び、誤った同一性判定をせず全レコードをレビュー可能にする。

**Architecture:** 既存triageは過去の本文言及として追加。別のHTML読取補助で見出し・区切りレコード・語境界一致位置を固定baselineの要確認2429件へ添える。公開判定は変更しない。

**Tech Stack:** Python標準HTMLParser/unittest/gzip/hashlib。既存fetch・triage。

**Spec:** `docs/superpowers/specs/2026-10-26-integrity-notice-context-design.md`

## Global Constraints

master/state/raw/Alias履歴/Apps Script不変、保持承認0、自動削除/解除なし、Phase 2なし。原本は公式通常GETの200だけを追加。本文言及を掲載名/Party ID/現在の状態の証明に昇格しない。

## Review Focus

ナビゲーション内の同名、関連先だけでの言及、SDNから別リストへの見出し切替、単一/二重brの混同、未知のHTMLと壊れた入力からの部分成功。これらをTask 1で直接検証する。

## Task 1: 読取補助と失敗時動作

Files: `src/integrity_notice_context.py`, `tests/test_integrity_notice_context.py`。
Interfaces: `read_contexts(body: bytes, names: set[str]) -> dict`。本文レコードとliteral spanだけを返す。`build_report(root, baseline_path) -> dict`はhash固定baselineとmanifest/原本を検証し、要確認分類2429件の文脈を作る。CLIはgzip JSONを排他的作成し、入力/解析不能は終了非0。

- [x] 本文外除外、書式タグ、段落/二重br区切り、リスト切替、未知見出し、単語内非一致、壊れたHTML、hash/path/baseline異常、排他出力を実データ型のテストで失敗再現。
- [x] 最小実装後、対象テスト成功。既存の監査関数・取込関数へ依存して読取結果を正当化しない。

## Task 2: 原本固定・実データ・レビュー・PR

Consumes Task 1 の `build_report`。既存triage manifestに2件を追加、元原本はgzipで別名保存する。

- [x] 全監査と文脈レポートを再実行し、変化をreview_id/根拠ごとに比較。候補一覧は全てPENDING。
- [x] 再実行でJSON一致・原本hash一致・既存証跡と業務データ不変を確認。
- [x] 全unittestとoffline、独立レビュー、結果資料、PR更新を完了。BLOCKED/Draft維持。

Ruling: 追加の原本追跡を依頼されたため、既存Phase 1の範囲で補助文脈を実装する。語境界一致は人間の確認候補であり役割や同一性を決めない。誤読すると保持判断を誤るため、原文レコードを残し未承認の停止を保つ。

Final review: 表のセル内ブロック/二重brでの部分分割、不正な親子構造/void終了の部分成功を実テストでRED→GREEN。全566/566。Minor deferred: 見出しbrの空白表示、現3原本には無し。baseline overrideは管理された研究入力用と資料に明記。
