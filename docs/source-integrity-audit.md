# 一次ソース完全性監査 — Phase 1

## 目的と既存機能との違い

`source_audit`は取得のHTTP情報、`mof_record_diff`は原本間の変更、OFACの既存監視はClassic/AdvancedのParty集合を監査する。この追加機能は、取得済みの公式原本から、実際に公開するmaster候補とOFAC Party/Alias履歴への対応を独立に調べる。ネットワークアクセスは行わない。

- OFAC XML: `xml.sax`による独立したstreaming reader。本体のElementTreeパーサ、`ofac.parse`、`parse_advanced`、`ofac_index.update`は期待値生成に使用しない。
- OFAC Classic: CSV各行を直接読み、Party ID・主名称・別名をXMLと照合。Classic主名称はXMLの`Alias.Primary`とは別の意味なので混同しない。
- 財務省: 32列CSVと番号の一意性、氏名・別名・旧称・Weak欄、明示されたoriginal-script原綴りを別実装で読む。不正括弧・列変更・空の対象者は解析不能。
- `match_key`と表示名の正規化はmasterとの接続契約として共有する。したがって正規化や名寄せ品質全体の独立証明ではない。

## 公開ゲート

`.github/actions/run-watch/action.yml`で、取得後かつExcel再生成・commit・release・差分通知より前に必須実行する。取得自体が失敗した場合も`UPSTREAM_FAILED`を付け、旧snapshotだけを検査して今回の成功とはしない。rebase後はpush前に再監査する。Excelはrebase・再監査後に確定したmasterから生成し、古い候補から作った配布物を残さない。

監査は候補ファイルを読み取るだけで、master・state・Alias履歴・既存監査台帳を書き換えない。非ゼロ終了は公開停止の指示であり、削除や制裁解除の指示ではない。通常のCLI `src.watch`単体は候補生成ツールのままであり、手動配布時も必ずこの監査を実行すること。

新規bootstrapでも完全性を確認する。原本・state・OFAC履歴が無い旧Excelだけのbootstrapは公開不可になる。原本との対応を整えた候補を先に準備する必要がある。

監査レポートは`data/integrity_audit/`へ毎回別名のgzip JSONとして保存し、過去のレポートを上書きしない。成功・失敗ともActions Artifactに90日保存する。毎時巨大なJSONをgit履歴へ積まないため、自動生成分はgitignoreする。90日を超える証跡が必要ならArtifact失効前に保管する。今回の実データbaselineは`docs/evidence/`に固定している。

## 判定と証跡

| 分類 | 内容 |
| --- | --- |
| MISSING_REGISTRATION | 公式主名称に対応するmaster行が無い |
| MISSING_ALIAS | 公式Alias/旧称/原綴りに対応するmaster行が無い |
| EXTRA_REGISTRATION | 現在原本に根拠を確認できない出典付き登録。旧名・過去掲載・意図的保持の可能性があり、誤登録/制裁解除と断定しない |
| MISGENERATED | 表示名またはAlias名称と保存キーが不一致。誤生成・正規化不整合の疑義 |
| SOURCE_MISMATCH | 名前は存在するが公式出典/カテゴリが対応しない |
| MISSING_ALIAS_EVIDENCE | (list, Party ID, name)の履歴リンク欠落 |
| SOURCE_ID_MISMATCH | 現役Party IDまたはAliasのID関係が公式原本に存在しない |
| STATUS_MISMATCH | 原本掲載状態とmaster/Party/Alias現役フラグが不一致 |
| ALIAS_QUALITY_MISMATCH | XML Primary/WeakとClassicとのunion結果が履歴の品質区分と不一致 |
| WEAK_ALIAS_ACTIVE | OFAC Weak-only名称がOFAC単独の出典で通常masterに有効登録されている。自動無効化はしない |
| DUPLICATE_RECORD | masterまたは履歴のキー重複 |
| UNDETERMINED | 原本不在/hash不一致、未知の構造、曖昧な名称欄、入力破損等 |
| UPSTREAM_FAILED | 今回の取得処理が失敗 |

いずれも現在は公開を止める。意図的な保持行に対する自動免除は設けない。レポートには検査対象原本のURL/SHA256/path、master/state/indexのSHA256、全公式名称の番号・Party ID・該当箇所とmasterキーへの対応、検出された原本/登録行を含む。余分な登録には対応原本が存在しないため、レポートのdocuments一覧が「どの原本集合を調べて不存在と判断したか」の根拠になる。

## 2026-10-23 実データ監査

調査基点はmain `ea4ac7f0319ea3ea285d8d26c77be1fd17e92cda`。リポジトリに保存されたstateとそのSHA256に一致する公式原本7ファイルを使用した。ウェブサイトの現時点の最新性を再取得で確認したという意味ではない。外務省・経産省へはアクセスしていない。

| 対象 | 原本照合数 |
| --- | ---: |
| master | 89,341行 |
| 財務省 2026-10-21版 | 2,900レコード / 名称証跡15,693件 |
| OFAC SDN | 19,471 Party / Classic主名称19,471 / Classic別名20,192 / XML名称50,081 |
| OFAC Consolidated | 486 Party / Classic主名称486 / Classic別名1,115 / XML名称1,798 |
| 名称証跡合計 | 108,836件（重複・複数形式を含む。対象者数ではない） |

独立リーダーはXMLで重複出現する同一名称も保持するので、本体のdedup後の名称件数とは一致する必要がない。Party集合は両リストでClassic/XML一致。現在のParty/Alias履歴の不足・ID不一致・品質不一致は今回0件。公式名称のmaster未登録も今回0件。ただし全体判定は **BLOCKED**。

| 検出 | 件数 | 解釈 |
| --- | ---: | --- |
| EXTRA_REGISTRATION | 7,027 | 全件OFAC出典。現在原本に対応しない有効登録。意図的な旧登録保持を含み得るため要レビュー |
| WEAK_ALIAS_ACTIVE | 2,878 | OFAC単独出典で有効なWeak-only名称。既存方針ではactive行を自動無効化しないため蓄積し得る |
| SOURCE_MISMATCH | 8 | 財務省の区分35/36のカテゴリ不足。MRB Bank関連6名称、Rim Yong Hyok関連2名称 |
| MISGENERATED | 1 | CHERNOMORSKI BANK RAZVITIYA I REKONSTRUKTSII関連行の表示名とキーが不一致 |

これは9,914件の検出事象であり、9,914人の誤登録が確定したという意味ではない。同じ行が複数の問題に該当し得る。今回の実装では既存登録を変更・削除していない。この状態でPRをmergeすると対象監視Workflowの新しい公開が停止する。運用投入前に保持行の扱い、Weak-only行、8件のカテゴリと1件のキーをレビューする必要がある。

### 過去の4名

| 原本名称 | 財務省番号 / 国連参照番号 | 位置付け | 現行master |
| --- | --- | --- | --- |
| SEBASTIEN UWIMBABAZI | 009-000046 / CDi.046 | 主名称 | 有効・財務省出典あり |
| MUHAMMED LUMISA | 009-000047 / CDi.047 | 主名称 | 有効・財務省出典あり |
| LUMISA, MUHAMAD | 009-000047 / CDi.047 | 同一人物のStrong Alias | 有効・財務省出典あり |
| CHARLES SEMATAMA | 009-000048 / CDi.048 | 主名称 | 有効・財務省出典あり |

原本SHA256: `f8e1038419ab0146cbce89ae9fa7b5f9d298548c8bca4953f970c01f87b3f7bc`。
保存済みの実データを一時コピーし、この4キーだけをmasterから除外すると、主名称欠落3件・Alias欠落1件を検出してBLOCKEDになる。元masterは不変。詳細は`docs/evidence/source-integrity-four-name-mutation.json`。

### 財務省IDに関する限界

masterの現行スキーマには財務省番号/国連参照番号を保存する列が無い。監査レポートは原本の番号と名前・出典・カテゴリから対応を再構成するが、これは「master内に独立して保存されたParty IDとの照合」ではない。ソース別IDを持つ正式な登録台帳が無い限り、この点を完全検証済みとはしない。OFACでは既存のParty/Alias履歴のIDと直接照合する。

## 再現とテスト

このPRのcheckoutで実行する。state/master/rawを別の世代に入れ替えると結果も変わる。

```bash
python -m pip install -r requirements.txt
python -m unittest
python -m tests.test_offline
python -m tests.test_mof_record_diff
python -m tests.test_mof_re_review
python -m src.integrity_audit --sources all
```

最終コマンドはbaselineの不整合を検出し、終了コード1となるのが期待結果。要約は`docs/evidence/source-integrity-20261023-summary.json`、全証跡は`docs/evidence/source-integrity-20261023.json.gz`。gzipを展開してJSONとして読む。source hash/input hashおよび展開後JSON全体のhashを要約に固定している。レポート内容は同じ入力なら同じになる（出力ファイル名だけが実行ごとに異なる）。

検証結果: 新規41 testsを含むunittest 486/486、既存offline 213/213、財務省差分18/18、再審査7/7。Nodeのcrypto dashboard（30）、MOFA GAS、OFAC watchdog/statusの全スクリプト、およびcrypto publication shellテストが通過。

## 独立レビュー

別エージェントによるread-onlyレビューを実施。相対rootでのcacheパス解決、Identity内の部分的XML構造変更、未知の履歴リスト名、余分な出典カテゴリ、破損deflateの証跡欠落を再現テストでRED確認後に修正した。旧登録のBLOCKED判定、財務省ID対応の限界、正規化契約の共有は意図した仕様として維持した。ウェブ原本の最新性や個別制裁の法的有効性はこの実装レビューの対象外。
