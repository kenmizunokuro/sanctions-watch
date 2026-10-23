# 過去原本回収 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Research and implementation stay on the existing isolated feature branch.

**Goal:** Phase 1の未確認登録をGit原本で再調査し、固定証跡を日々の監査へ接続する。

**Architecture:** Git履歴調査・使い捨て回収実験・通常監査を分ける。固定原本を独立リーダーで読むcollectorだけを追加し、承認処理は変更しない。

**Tech Stack:** Python標準ライブラリ、Git、既存independent readers、unittest/bash。

**Spec:** `docs/superpowers/specs/2026-10-24-integrity-history-design.md`

## Global Constraints

本番master/state/raw/Alias履歴/Apps Scriptは変更しない。保持承認・削除・解除・Phase 2を行わない。旧証跡は残す。

## Review Focus

マージ時だけの原本、旧不正キー、baselineへの原本混入、固定証跡の消失/破損/パス異常、既承認でも解析失敗時に証跡を残して止めること。

## Tasks / ledger

- [x] Git祖先を取得し、36 masterスナップショット・110原本出現箇所/72本文を調査する。名前と保存キー、原本名と履歴Party IDを別々に記録する。
- [x] `docs/evidence/reproduce-integrity-{history,recovery}-20261024.py` を作成し、全baselineを固定して153件の分類変更を再現する。
- [x] `tests/test_integrity_history_evidence.py` でマージ原本・不正キー・余分なbaseline原本を失敗再現し、修正後に通過を確認する。
- [x] `tests/test_integrity_triage.py` に固定原本の読取・hash/解析/パス/欠損失敗と実ゲートのテストを追加する。RED→GREENで `src/integrity_triage.py` のcollectorを拡張する。
- [x] 過去Gitのgzip blob3本を監査用領域に保存し、現在の全監査・分類・PENDING候補を別ファイルで保存する。masterは変更しない。
- [x] 独立レビューを実施する。原本blob一致とcurrent=falseを確認する。symlink循環で監査証跡が失われる指摘は重要な要件違反と判断して実ゲートのRED→GREENで修正する。
- [x] 全Python532/532・offline213/213を確認する。実データBLOCKEDを期待結果として検証し、同一入力の再現性を比較する。
- [x] 現PRへの追加コミットを準備する。GitHub反映・CI確認の結果はPR本文と最終報告に記録する。Draftのまま、マージしない。

Ruling: 単純なraw復元ではpruneで原本が再び消えるため、固定証跡を採用。誤った由来情報を固定しないよう元Git blob・本文hash・URLを保存し、独立レビューでも一致を確認する。

Ruling: symlink循環の指摘は公開停止自体を破らないが、失敗証跡を失うので修正対象に格上げする。RuntimeErrorをTriageErrorへ変換し、ゲートが失敗JSONを保存するテストを追加する。

研究レポートの追加原本件数は意味関係ごとの選択証拠を数える。支持本文総数とは主張しない。回収実験の成果物は分類差分と全manifest、変更の無い項目はhash固定した前回成果物を参照するという範囲である。
