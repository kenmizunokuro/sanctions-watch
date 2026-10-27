# Integrity Triage Implementation Plan

Goal: 誤判定を修正し、既存findingを根拠とともに分類して、具体的な保持判断候補へ絞る。
Spec: docs/superpowers/specs/2026-10-23-integrity-triage-design.md
Execution: ユーザーの「では進めて」に基づき同一PRのbranchで継続。本番データは不変。

- [x] Task 1: empty category mapの誤判定とquoted surname key生成をテストで再現。カテゴリ表示未確定と取得/解析不能の停止条件を区別。既存キーの自動移行はしない。
- [x] Task 2: src/integrity_triage.pyで全findingを分類。current/archived official witness、Alias状態、名前でないfield、元rowのhashを保存。旧キー衝突は統合案。過去原本不在を隠さない。tests/test_integrity_triage.pyに一意ID・古い入力hash・同名別Party・非変更性・不正raw構造・カンマ表記根拠を固定。
- [x] Task 3: 保持承認はexact finding/master/Party/evidence hashに固定。分類だけでは免除せず明示承認のみ。欠落/原本異常等の重大findingは承認対象外。空の承認候補だけを出す。testsで変更された行/根拠/ID、新規問題は承認が効かないことを検証。
- [x] Task 4: 実データ再分類、全オフライン/Node/Shell/ゲートテスト、独立レビュー、証跡・説明とPR更新。本番には反映しない。


実行記録: 8件は原本/出典欠落ではなく空のカテゴリ対応の監査誤判定。旧キーは引用符と姓名順の処理順に起因し、正規キーに既存行があるため統合案のみ。42原本+1補助発表で9,914事象を分類し、3,139件を4つの未承認レビュー群へ整理。5,743件の由来未確認等は免除しない。重大findingは保持承認不可。相対root・再実行決定性・実際のbashゲートで承認保持と新規欠落/原本破損の併存も検証。元業務データは不変。


最終独立レビュー: ソース欠落によりOFAC原本でMOF保持を承認できる問題を、EXTRA findingのchecked_source、review_idへの固定、OFAC限定の多重ガードで修正（RED→GREEN）。不正な日付だけのZ表記は厳密UTC日時として拒否し、failure JSONを保存（RED→GREEN）。承認済み表示の矛盾も修正。最終unittest518/518、offline213/213、MOF18/7、全Node/shell PASS。未承認0の実データは引き続きBLOCKED、旧業務データ変更なし。
