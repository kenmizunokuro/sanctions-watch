# Source Integrity Implementation Plan

> 実行: このセッションで設計・実装・検証・PRまで進めるという依頼に従い、追加承認待ちは設けず専用ブランチで実行する。

Goal: 独立した原本読取りによる公開前完全性監査。
Architecture: readers → engine → immutable JSON report → publication gate。
Tech Stack: Python stdlib csv/xml.sax/unittest、既存match_key。
Spec: docs/superpowers/specs/2026-10-23-source-integrity-design.md

Global Constraints: master/Apps Script本番変更禁止、自動削除禁止、METI/MOFA取得禁止、既存証跡維持、Phase 1のみ。
Review Focus: 304時の破損cache、同名別Party、WeakとStrong重複、CSV列/括弧構造変更、取得失敗後の旧snapshot正常誤認。

## Task 1: 独立リーダー
- [x] tests/test_integrity_readers.pyにXML script別組立て・False Identity・Weak/Primary・重複ID・空/未知構造・MOF番号と別名と不正括弧を固定。追加前に失敗を確認。
- [x] src/integrity_readers.py: read_ofac_xml(body, label), read_classic(body, label, alias), read_mof(body)。既存sourcesパーサを呼ばない。
- [x] 四名は原本3レコードのfixtureで主名称/別名/番号を確認。

## Task 2: 監査エンジン
- [x] tests/test_integrity_audit.py: 欠落/Alias欠落/出典不一致/ID不整合/誤生成/Weak/保持行/入力hash不一致/原本不在/再現性/非変更性のRED→GREEN。
- [x] src/integrity_audit.py: audit(root, sources, upstream_failed=False), write_report, CLI。master/state/indexはcsvを直接読み、重複行も落とさない。原本をhashで特定し完全一致を要求。レポートは別ファイル、失敗は非ゼロ。

## Task 3: 公開ゲート・実データ検証
- [x] workflow境界のテストを先に追加。
- [x] run-watch actionに監査とalways artifact、bootstrapに監査を追加。失敗時に後続commit/releaseは動作しない。
- [x] 依存を満たした全unittestと既存独自/Node/shellテスト、保存済み実原本で監査。実問題は修正/自動削除せず分類と証跡を報告。
- [x] docs/source-integrity-audit.mdに再現コマンド、結果、限界を記録。自己点検後に専用branchをpushしPR作成（mergeしない）。

## 実行記録

- Task 1/2/3: complete。main ea4ac7fを専用checkout/branchに固定。既存445 unittest baseline PASS（初回のCrypto依存不足はvenv requirements導入で解消）。
- 旧登録の保持を自動免除せず全findingで公開停止。保持行の扱いは本番反映前に利用者レビューが必要。誤った免除による見逃しを避ける判断。
- 財務省IDは既存masterに列が無いため独立検証できず、再構成リンクとして明記。独立ID台帳導入まで検証範囲に限界が残る。
- 最終read-only独立レビュー: Important 4件と破損deflateの証跡不備を全てRED→GREENで修正。Excel生成をrebase後へ移動し、配布世代の取り違えも防止。
- 最終確認: 41新規testsを含む486 unittest、offline213、MOF18/7、全Nodeとcrypto publication shell PASS。実データ監査は問題を検出するため期待どおりBLOCKED。元master/state/raw/index・既存監査台帳は変更なし。
