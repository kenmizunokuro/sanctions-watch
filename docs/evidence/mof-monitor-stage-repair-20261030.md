# 財務省の取得状況と反映停止の分離（2026-10-30）

## 原因と再現

実行 `40993955902` は10月30日版CSVの取得・解析・原本差分生成に成功した後、完全性監査で停止した。

- 原本SHA256: `1ef25737e3f44e99f830cc47ac5cfa96c18b4cef1ab6d53755ad339f29eb9bf9`
- 原本: https://www.mof.go.jp/policy/international_policy/gaitame_kawase/gaitame/economic_sanctions/shisantouketsu20261030.csv
- 実行: https://github.com/kenmizunokuro/sanctions-watch/actions/runs/40993955902
- 原本差分: 削除3レコード、情報改訂4レコード（36項目）、計39候補行。追加レコードは0。
- 監査: `LEGACY_ACCEPTANCE_INVALID` / `legacy candidate annotation missing, changed, or duplicated`。
- 失敗時刻: 2026-10-30 11:55:46 JST。以前の取得済み・反映済み原本は10月21日版。

保存済み原本を再解析し、失敗ログと同じ時刻でマージした候補masterのSHA256は、失敗時の監査入力と完全一致した。修正後の分類結果は `integrity_blocked`。原本の取得成功と監査による公開停止は両立する。

## 要レビューの内容

次の6照合文字列で `sources: OFAC;財務省 → OFAC`、`categories: 財務省:タリバーン関係者等 → 空` を検出した。状態は有効のまま。既存の未確認旧登録に対する期限付き承認はこれらの変更を許可しておらず、厳密な照合で止まること自体は正しい。

| 照合文字列 | 変更 |
|---|---|
| Abu Ahmad Akhlaq | 財務省の出典・カテゴリが外れる候補 |
| Abu Ahmad al-Shami | 同上 |
| Abu al-Darda' | 同上 |
| BASSAM AHMAD AL-HASRI | 同上 |
| Bassam Ahmad Husari | 同上 |
| IYAD NAZMI SALIH KHALIL | 同上 |

これは6人の制裁解除、OFACの現行掲載確認、既存の期限付き保持の更新・延長を意味しない。レビューCSVは `data/dashboard/mof_legacy_review.csv`。

## 修正

`mof_pending` は原本の解析・ハッシュ照合・差分生成が完了した時点だけ、実行IDと試行番号に結び付いた取得証跡を作る。失敗処理は実際のステップ結果、原本、候補CSV、候補masterに結び付く監査報告を照合し、通信失敗・監査停止・承認待ち・処理異常を区別する。

`status.csv` は旧Apps Scriptが警告を失わないよう従来のエラー表示を維持。v0.5.1は `status_detail.csv` を読み、同じ時刻・反映済み原本の世代であるときのみ「取得済・監査停止（master未反映）」等を表示する。監査停止は重大警告のまま、監視遅延も併記する。件数・最終更新・ハッシュは反映済み原本の情報を維持する。

両ゲート通過後の障害は、配布や通知が失敗してもmasterが既に公開されている可能性があるため「未反映」と断定しない。承認ゲートは候補0行でもmaster差分があれば承認待ちとして記録する。

## 利用者側の反映

Apps Scriptの `コード.gs`（GitHubの `apps_script/Code.gs`）をv0.5.1に差し替え、保存後に制裁監視の「今すぐ同期」を実行する。旧版のままでは従来のエラー表示が残る。候補の承認・マスター反映を行う操作ではない。

## 保持した制御

完全性監査、未承認差分の公開禁止、承認期限、元のマスター、反映済み原本のstate、正式変更履歴、検索・スクリーニング出力は変更しない。今回の修正は停止理由と取得証跡の修正であり、6件の承認や本番取込の完了ではない。

## 検証結果

Python unittest discover: 664件通過。既存offline: 213/213。GAS Nodeテスト: 13/13。原本Artifactの再現で候補master一致・39候補・integrity_blockedを確認。隔離したローカルgitリポジトリで失敗証跡の公開経路を実行し、業務データに変更がないことを検証。独立コードレビューの指摘2点を修正後、再レビューで承認済み。
