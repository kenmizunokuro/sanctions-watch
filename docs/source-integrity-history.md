# Phase 1 過去原本の回収と登録経路の追跡

2026年10月24日（日本時間）の追補。前回の原本未確認149件について、Git履歴に残っていた公式原本を回収し、名称と履歴のParty IDを確認できた。さらに由来未確認群の4名称に過去原本の名称根拠が見つかった。承認は作成せず、公開判定はBLOCKEDのまま。

## 調査で確定したこと

固定コミット `ea4ac7f0319ea3ea285d8d26c77be1fd17e92cda` の祖先を取得し、masterのfirst-parent履歴36スナップショットとOFAC原本110出現箇所・72種類の本文を調べた。マージ各親との差分を含め、同一本文はSHA256で重複排除した。72本文の解析失敗は0件。新しいリモート原本の取得ではなく、保存済みGitオブジェクトの調査である。

前回の由来未確認5,743名称は、すべて初回masterコミット `d88c913aab4b5754c396c541a3b496629aac880d` に**同じ表示名称・OFAC出典付き**で存在した。監視導入後に初めて生成された名称ではなかった。初回masterの出典欄は過去の登録主張であり、公式掲載の独立した証明には使わない。原始投入Excelはリポジトリ履歴にも確認できず、初回からあるdist ExcelはREADME上masterの生成物である。

## 回収した153件

| 前回分類 | 今回確認できた関係 | 件数 |
| --- | --- | ---: |
| 履歴はあるが保存原本未確認 | 過去Partyの名称と履歴Party IDが一致 | 132 |
| 履歴はあるが保存原本未確認 | 現役Partyの過去Aliasと履歴Party IDが一致 | 17 |
| 由来未確認 | 過去原本に名称あり、master側の履歴対応は無し | 4 |

最後の4名称は、2026-08-29保存のClassic原本にParty ID **42734** として存在する。

- DULAC CAPITAL LTD（主名称）
- AK Dyulak Kepital Ltd Predstavitelstvo
- Predstavitelstvo Aktsionernogo Obshchestva Dyulak Kepital Ltd Shveitsariya v g Moscow
- Predstavitelstvo Aktsionernogo Obshchestva Dyulak Kepital Ltd Shveitsariya v g Sankt Peterburge

名称と原本上のParty関係は示せるが、masterには対応するOFAC履歴が無い。この4件は `ARCHIVED_NAME_WITHOUT_INDEX` とし、masterの人物・法人同一性がIDで独立確認されたとは扱わない。現在の制裁状態や保持の意図も自動判断しない。

## 継続して使える監査用原本

必要な原本は3ファイルに絞れた。通常の `data/raw` は保存数・容量によるpruneがあるため、単純に古いファイルを戻す方式は採用しなかった。Gitに元々ある同じgzip blobを `docs/evidence/integrity-archives/` に保存し、manifestに公式URL・本文SHA256・元コミット/パス/blobを固定した。既存のraw、state、master、Alias履歴、Apps Scriptは変更していない。

| 元原本 | 本文SHA256 |
| --- | --- |
| 20260829T214252Z__alt.csv | `a4cae601ae9cd8ef977dc08da48d47a2cc59a1383cc0677fa812421b48025e39` |
| 20260829T214252Z__sdn.csv | `a2ccea04237a13459009bc527502bb6a08d4e84fe97b46fcce1ed621e9a8301e` |
| 20260904T010536Z__sdn_advanced.xml | `657e2ecd8ed722d5640e4076058a24982c6e722aac2a631eea3da1e052861f93` |

`src/integrity_triage.py` がこのmanifestを読む。既存取込パーサを使わず、これまでと同じ独立Classic/XMLリーダーで解析する。常に過去資料（current=false）として扱い、現在の掲載証拠へ昇格しない。manifest構造不明、重複、領域外パス、非公式URL、不正hash、欠損、hash不一致、解析不能はtriage失敗として公開を停止し、監査レポートを保存する。保持の承認済み行があっても破損原本を無視して公開しない。

## 回収後の実データ監査

これは使い捨てコピーだけの数字ではない。上記3原本を証跡領域に固定したPRのcheckoutで、既存の `python -m src.integrity_audit --sources all --triage` を実行した結果である。

| 分類 | 件数 | 扱い |
| --- | ---: | --- |
| 現役Weak-only | 2,878 | 保持レビュー候補 |
| 現行カンマ除去表記 | 41 | 保持レビュー候補 |
| 過去Partyを原本・履歴IDで確認 | 335 | 保持レビュー候補 |
| 過去Aliasを原本・履歴IDで確認 | 34 | 保持レビュー候補 |
| 過去名称原本あり・履歴対応なし | 4 | 同一性確認を伴う保持レビュー候補 |
| 由来未確認 | 5,739 | 元出典が必要 |
| 過去公式発表での言及のみ | 840 | 掲載関係の追加根拠が必要 |
| 呼出符号・船主等のみ | 33 | 名称欄としての追加根拠が必要 |
| 旧キー衝突 | 2事象（同じ1行） | 前回の統合案のみ。未適用 |
| 財務省区分表示名未確定 | 8 | 非ブロックの確認事項 |

検出事象の合計は9,914件のまま。名称/Alias欠落、ID/品質不一致、現行原本解析失敗は今回も0件。原本付き保持候補は**3,292件・5グループ、承認0件**。それ以外の名称根拠未確定群は6,612件。旧キー不整合と表示ラベル8件はこの6,612件に含めていない。未承認・未解決のブロック事象は9,906件、終了コード1/BLOCKEDを維持する。

追加の名称/Party関係によりreview_idが182件更新された。前回の候補ファイルも証跡として残すが、今後のレビューには `source-integrity-20261024-retention-review-groups.json` と `source-integrity-20261024-pinned-triage.json.gz` を使う。旧IDを新IDへ自動移植したり、候補一覧を承認ファイルとして読んだりしない。

## 証跡と再現

- `docs/evidence/source-integrity-20261024-history.json.gz` と `history-summary.json`（同じ日付prefix）：9,914事象ごとの初回登録候補・最初のOFAC主張・過去原本関係。名前正規化の対応と保存キーの対応を区別する。
- `docs/evidence/source-integrity-20261024-recovery{.json.gz,-summary.json}`：3原本を使い捨てコピーへ戻す再実行の153分類差分と全原本manifest。変更の無い項目はhash固定した前回の証跡を参照する。全triageのdigestも記録し、再実行で再生成できる。
- `docs/evidence/source-integrity-20261024-pinned-{audit,triage}.json.gz` と `pinned-triage.csv.gz`：PR checkoutでの実際の監査・全分類。
- `docs/evidence/source-integrity-20261024-pinned-summary.json`、`source-integrity-20261024-retention-review-groups.json`：実監査要約・未承認候補。
- `docs/evidence/source-integrity-20261024-evidence-needed.csv.gz`：追加資料が必要な6,612件。対象名・出典主張・初回master参照URLを付け、元原本を確認するための一覧とした。

完全なGit履歴を取得したcheckoutで、次のコマンドを実行する。調査用出力先は新しいディレクトリを指定する。既存証跡への上書きは拒否する。

```bash
python docs/evidence/reproduce-integrity-history-20261024.py --output-dir /tmp/integrity-history-new
python docs/evidence/reproduce-integrity-recovery-20261024.py --output-dir /tmp/integrity-recovery-new
python -m src.integrity_audit --sources all --triage
```

履歴調査は固定された前回の9,914件を入力とする。通常監査は現checkoutの入力を使う。回収実験は入力CSV/stateだけでなく、前回の全raw/補助発表のファイル集合・本文hash・発表manifestを照合してから行う。第四の原本の混入や既存原本の変更を、3原本の回収効果に混ぜない。

再現結果には実行時刻を入れず、同じGit/入力では同じ内容になる。gzipヘッダーのPython/zlib実装差を除き、JSON本文で比較できる。履歴レポートの `additional_archived_body_candidates` は各名称/Party関係で選んだ最初の証拠が追加本文に由来する件数であり、全ての支持本文数ではない。原本manifestには全出現箇所を残す。

履歴調査と使い捨て回収実験をそれぞれ2回実行し、JSON本文の完全一致を確認した。通常監査と全triageも2回実行してJSON内容が一致した。履歴JSON本文SHA256は `6373bdcb81e65da2a6c7d314ef84e2e65e61dde932bf4e8c29874f3ca0b6f59c`、回収差分は `2d4035e7e8eacbd4d711b244edfa22419dcab71e4865bedabee8c76eee80ad45`。再現性は完全性の独立証明の代用ではなく、同じ根拠から同じ調査結果を再生成できることの確認である。

## 検証と残る判断

Gitマージ時だけに追加した原本を拾うテスト、不正保存キーでも表示名称の原本を拾うテスト、回収前baselineへの余分な原本混入を拒否するテストを、失敗再現→修正→通過で確認した。固定原本のhash異常・欠損・manifest欠落・解析不能・パス逸脱も検証。実bashゲートで、通常rawから古い原本を取り除いても固定証跡があれば既承認の保持だけはREVIEWで通過し、固定証跡が壊れた場合は未公開のまま監査JSONを保存することを確認した。

Python全unittest **532/532**、既存offline **213/213**。今回追加した研究用4件、固定原本・ゲート10件を含む。独立レビューで見つかったsymlink循環時の例外は、公開停止に加えて失敗証跡も残すという要件から修正対象とし、実bashゲートで証跡欠落を再現してから修正した。原本3ファイルのGit blob一致・過去資料扱いもレビューで確認した。保持判断、初回投入者の意図、現在のリモート掲載内容をレビューで確定したものではない。

残る5,739名称については、初回投入者の元名簿・出典URL・元リストの保存版などが必要。初回masterのOFACラベルだけから保持承認や誤登録・掲載終了を確定しない。保持3,292件は人間レビュー待ち、旧重複キーの統合案も未適用。経産省・外務省へのアクセス、Phase 2、本番反映、削除・無効化は行っていない。
