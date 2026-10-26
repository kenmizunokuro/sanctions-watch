# Phase 1 明示姓名フィールドによる追加監査

次の追補：[過去の公式発表と未確認名称の文脈追跡](source-integrity-notice-context.md)。以下の数値は10月24日時点の証跡として保存する。

2026年10月24日（日本時間）の追補。前回の出典未確認5,739件のうち4,106件について、現行OFAC原本の明示された姓名部品との対応を確認した。新規登録・保持承認・masterの再採番は実施していない。公開判定は引き続きBLOCKED。

## 原因と実装

原本に無い名称とは限らず、名前の表現順が違うものが多数あった。Party39585の弱別名はXMLでLast Name=Peijnenburg / First Name=Aamと定義されている。物理順を連結した既存読取結果はPeijnenburg Aam、旧masterにはAam Peijnenburgがある。さらに共有正規化はKADKHODAEE, Abbas Aliの「Abbas」を法人接尾辞ABの前方一致と判定し、姓名順を変換しない。この問題は旧キーや他ソースにも関わるため、今回はグローバル正規化を変更していない。

`src/integrity_person_forms.py` に独立したSAXリーダーを追加した。取込用パーサは呼ばず、原本のPartyType / PartySubType、NamePartTypeとIdentityごとのNamePartGroupを参照する。明示されたIndividualで、同じDocumentedName・同じScriptにFirst Nameが1個、Middle Nameが0〜1個、Last Nameが1個ある場合に限り、名・ミドル・姓の表示候補を組み立てる。文字列の単語順を自由に置き換える方法やfuzzy照合は使わない。

これは**明示フィールドから得る派生表記**であり、原本に追加のAliasが掲載されているという意味ではない。同じ原本の解釈を追加したもので、別ソースでmasterの人物・法人同一性を確認した証明でもない。基本監査の必須名称集合や既存キーは変更せず、triageの既存行に対する根拠としてだけ参照する。

- 現行Advanced XMLだけが対象。過去資料をcurrentに昇格しない。
- 原本のParty ID、主名称/別名、Weak品質、Script、部品の型/値/Group ID、URL/hash/位置を保存する。
- Entity、False Identity、異なるScriptの混合、重複・不完全な部品、Maiden Name等の未対応組合せから表記を推測しない。型/部品から構成できない件数と理由をmanifestへ記録する。これは構成を見送ったもので、現在の名称照合結果を「変更なし」へ置き換える処理ではない。
- XML/namespace/version/真偽値の失敗や、参照宣言・NamePartGroups・名前欄の未知の親子構造はtriage失敗として停止する。未知の要素を途中で読み飛ばして残りを採用しない。
- 名前とParty IDだけでなく、部品の型と値もreview_idへ固定する。複数Partyの同名関係は全て残す。WeakをStrongへ昇格しない。

現行2本文で得られた表示候補はSDN18,510、Consolidated309。これは対象者数でも追加登録数でもない。今回の9,914事象中では、姓名順別表記4,187件に現在の部品根拠が付いた（前回の未確認4,106、発表言及のみ80、過去Alias扱い1）。既存Weak-onlyの16件にも型付き根拠が見つかり、うち3件は以下の衝突として扱う。

## Weak/Strongの同名衝突3件

| master表示名 | 原文順で一致したWeak Party | 型付き表記で一致した非Weak Party |
| --- | --- | --- |
| Abu Faris | 23335 / 43817 | 13367 |
| ABU KHALID | 9595 | 2681 |
| ABU TAHIR | 22267 | 57424 |

異なるPartyに同じ表記があるため、元行の人物同一性を推測しない。`WEAK_EVIDENCE_CONFLICT` として保持承認の対象外にした。正しい形式の承認行を作っても、この衝突を通常のWeak保持として免除できないことをテストした。原本・元行・各Party関係は `docs/evidence/source-integrity-20261024-person-conflicts.json` に保存した。

## 今回の実データ結果

masterは89,341行、必須公式名称の監査関係は108,836件で前回と同じ。検出事象は9,914件のままで、分類と根拠を精密化した。名称/Alias欠落、Party ID/品質不一致、現行原本の解析失敗は今回も0件。ただし、3件のWeak判定には上記の別表記・別Partyとの衝突があり、通常保持と分けて停止する。

| 分類 | 件数 |
| --- | ---: |
| 現行の明示姓名部品による別表記・非Weak根拠あり | 3,518 |
| 現行の明示姓名部品による別表記・Weak根拠のみ | 669 |
| 原文順で確認できたWeak-only | 2,875 |
| 現行カンマ除去表記 | 41 |
| 過去Partyを原本・履歴IDで確認 | 335 |
| 過去Aliasを原本・履歴IDで確認 | 33 |
| 過去名称原本あり・履歴対応なし | 4 |
| 出典未確認 | 1,633 |
| 過去公式発表の言及のみ | 760 |
| 呼出符号・船主等のみ | 33 |
| Weak/Strong別Partyの衝突 | 3 |
| 旧キー衝突 | 2事象（同じ1行） |
| 財務省区分表示名未確定 | 8 |

保持レビュー候補は**7,475件・7グループ、承認0件**。残る追加資料・判断対象は2,429件（出典未確認1,633＋発表言及760＋非名称欄33＋衝突3）。旧キー問題と表示ラベル8件はこの2,429件に含めない。ブロック事象9,906件、終了コード1/BLOCKEDを維持している。全候補を承認した、同名の人物を特定した、という結果ではない。

## 共有masterの公開前ゲート

取得対象が財務省だけの実行でも、配布対象はOFACを含む共有masterである。従来のソース限定ゲートでは、財務省実行でOFACのAlias欠落、OFAC実行で財務省原本破損があっても公開まで到達できた。実bashで両方の誤通過を再現し、`.github/scripts/integrity-gate.sh` の公開前監査を常に `--sources all` にした。取得スケジュールは維持し、取得失敗のvetoも引き続き伝える。初回・rebase後・bootstrapの公開経路が両ソースを検査する。ソース限定CLIは調査用として残る。

両方の異常で公開用sentinelに到達しない回帰テストを追加した。完全性監査Artifactは成功・失敗共通で90日保持され、従来のOFAC失敗証跡も変更していない。これはArtifactの無期限保管を保証するものではない。別の独立レビューでも、このゲート修正に指摘事項がないことを確認した。

## 変更・証跡・再現

- 実装：`src/integrity_person_forms.py`、`src/integrity_triage.py`、`.github/scripts/integrity-gate.sh`。既存取込、基本監査、共有正規化、承認適用ロジックは変更していない。
- 回帰：`tests/test_integrity_person_forms.py`、`tests/test_integrity_history_evidence.py`、`tests/test_integrity_publication.py`。実原本2 Partyの名前/型宣言を文字列のまま抜粋したfixtureと出典hashを `tests/fixtures/integrity/ofac_person_parts.*` に保存。
- 実監査：`docs/evidence/source-integrity-20261024-person-audit.json.gz`、`person-triage.{json,csv}.gz`、`person-summary.json`（同じ日付prefix）。部品・原本URL/hash・該当箇所を含む全件結果。
- 未承認候補：`source-integrity-20261024-person-retention-review-groups.json`。前回までの候補/証跡は残してあるが、今後のレビューは今回のIDと根拠で行う。
- 残る対象：`source-integrity-20261024-person-evidence-needed.csv.gz`（2,429件）。

```bash
python -m unittest
python -m tests.test_offline
python -m src.integrity_audit --sources all --triage
```

最後のコマンドは現在の未承認入力で終了1/BLOCKEDが期待結果。実監査とtriageのJSON内容は3回の実行で一致した。これは同じ根拠から再現できることの確認であり、完全性の独立証明の代用ではない。

以前の3原本回収実験へ、新しいパーサの効果を混ぜないよう、`reproduce-integrity-recovery-20261024.py` にパーサの意味的baseline照合を追加した。元の実験はコミット `3d2ad868cd8ada8e5b3afe8d2cbb18f3feb78309` のコードで再現する。現在のコードではbaselineが違えば明示的に拒否し、153件の回収効果という誤った報告を作らない。

## 検証と限界

Python全 **552/552**、既存offline **213/213**。元の4名称回帰も含む。新規テストでは原本の型/品質/Party保持、Entity/False Identity/異Script/曖昧部品の除外、同名別Party、部品変更による承認ID失効を確認。実bashゲートでは基本監査がPASSする入力でも、新メタデータが衝突・未知構造なら停止して失敗JSONを保存することを確認した。

独立レビューで、未知のNamePartGroupsラッパーや参照型名の内側の未知XML要素を読み飛ばせる問題を指摘された。ゲートが通ってしまう失敗テストを先に確認し、親子構造の検証を追加して全テスト通過を確認した。未判定だった公式schemaの全将来形や現在のリモート掲載内容まで完全に検証したとは主張しない。

共有正規化の法人接尾辞前方一致の問題は未修正。既存キーの移行・重複整理を行うには別途影響確認とmaster変更の承認が必要で、今回は部品に基づく監査根拠でこの誤分類を減らした。残る1,633件は元名簿/元出典等、Weak/Strong衝突3件は元対象の同一性レビューが必要。会社Apps Script、本番master/state/raw/Alias履歴、自動削除・解除、Phase 2には変更を加えていない。

最新main `901bb5c6` までの差分は暗号資産・監視状態等のデータ更新で、今回の監査対象master/原本・取込コードの変更はなかった。専用ブランチで実装し、mainへの取り込み・本番反映はしていない。
