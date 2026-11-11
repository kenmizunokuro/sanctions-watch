# 国連制裁監視

## 対象と正本

- [安保理統合制裁リスト](https://main.un.org/securitycouncil/en/content/un-sc-consolidated-list)
- [公式XML](https://scsanctions.un.org/resources/xml/en/name/consolidated.xml)
- 統合リストに対応する14委員会の公式発表一覧。現在の先頭ページを15分間隔で確認する。
- [1718委員会の指定船舶等の資料入口](https://main.un.org/securitycouncil/en/sanctions/1718/materials)は別枠の参照先。指定船舶そのものの一覧監視・master取込は含まない。

委員会決定による変更もあるため、決議本文だけを監視しない。新制度のPRN prefix、別名品質、構造変更、件数20%超減少は停止して確認する。委員会IDの更新・新設はコード変更でレビューする。

各制度の措置内容は同一ではない。国連掲載を日本国内の法的な適用判断・自動凍結判断と同一視しない。財務省等との各出典は独立して保持する。

## 定期監視

`watch-un.yml`は毎時08/23/38/53分（UTC）に起動予定。GitHub scheduleの起動時刻・15分以内の検知は保証されない。45分で警告、90分で重大表示する。現行のOFAC専用GAS救済は国連を起動しない。

```bash
python -m src.un_watch
python -m src.un_review status
```

取得・解析のいずれかが失敗するとCLIとworkflowは失敗する。正常に解析した他の国連資料は保存し、失敗した資料の直前有効snapshotは保持する。監視workflowはmasterを変更しない。

XMLの`dateGenerated`はファイル生成日時であり、個別対象者の制裁発効日時ではない。個別の`LISTED_ON`も原文の精度で保持し、推測の日時を作らない。HTTPのETag/Last-Modifiedは実際に返った値だけを記録する。

## 保存先

| パス | 内容 |
|---|---|
| `data/raw/un/` | 正常・解析失敗を含む取得原本。SHA256名・決定論的gzip |
| `data/un/state.json` | 資料別キャッシュ、監視結果、XMLイベント、発表イベント、レビュー履歴 |
| `data/un/snapshot.json` | 現在の検証済個人・団体snapshot |
| `data/un/snapshots/` | 歴代の検証済snapshot |
| `data/un/name_history.csv` | PRN別の本名・原語・別名、Weak/current、初回/最終確認、原本根拠 |
| `data/un/apply_audit.jsonl` | 明示適用したイベント・承認者・適用者・master前後hash |
| `data/dashboard/un_reviews.csv` | 未解消のXML・発表・監視範囲レビュー |
| `data/source_audit/YYYY-MM.csv` | 取得URL、HTTP、hash、件数、差分、取得/解析エラー |

一時署名URLのqueryは公開台帳から除く。公式配布入口を毎回参照し、期限付きURLを設定に固定しない。304でも原本hashを検証して再解析する。

XMLはPRNで比較し、氏名以外の識別情報改訂も変更として検出する。A→B→Aは別イベントとなり、過去の承認を再利用しない。別名Lowは証跡だけに保存し、国連由来の通常masterへ追加しない。

各XMLイベントは前回観測との差分に加えて、最後に実際に反映したsnapshotとの`application_diff`を保持する。複数の未反映更新を飛び越えて承認するときも、掲載終了候補や別名減少を確認できる。レビューCSVは集計行とPRNごとの明細行を持つ。明細のIDは`XMLイベントID:PRN`で、承認コマンドにはstatusが示すXMLイベントIDを使う。変更前後の名称・別名品質はstate内の`application_diff`、その他の識別情報は対応する新旧snapshotの`evidence`で確認する。

## 発表の監視範囲

初回にページ内の既存発表をbaselineとして保存し、新規指定とは数えない。過去アーカイブ全件を遡って確認したという意味ではない。

先頭ページの前回項目と重複が全く無くなったら、監視停止期間・大量発表・ページ構造変更の可能性があるため`coverage_gaps`を保持してエラーにする。次の正常取得だけでは消さない。公式のページ送り・履歴を人が確認し、期間と確認範囲を理由に記載して解除する。

```bash
python -m src.un_review coverage-review --committee 1267 \
  --reviewer '確認者名' --at '2026-10-27T15:00:00+09:00' \
  --note '実際に確認した期間・ページ・未取得発表への対応'
```

全発表をセンサーにするため、指定変更以外の委員会活動も候補に含む。新着・改訂発表を見つけてもXML反映済みとは判定しない。内容とXMLへの反映状況を確認した後、発表イベントごとに確認する。

```bash
python -m src.un_review notice-review --event-id EVENT_ID --index-hash INDEX_SHA256 \
  --reviewer '確認者名' --at '2026-10-27T15:00:00+09:00' \
  --note '発表本文・変更対象者・XMLへの反映状況を確認した結果'
```

発表本文・決議本文のURLは根拠として参照し、本文の自動取得・PDF解析までは行わない。

## XMLの承認とmaster反映

statusに出る現在の`event_id`、`source_hash`、`master_hash`を実値に置き換える。初回は1,000対象程度とその別名を含むため、対象者数と名称数を区別して確認する。掲載終了・別名減少も同じsnapshotの承認範囲に含まれる。

```bash
python -m src.un_review approve --event-id EVENT_ID --source-hash SOURCE_SHA256 \
  --reviewer '確認者名' --at '2026-10-27T15:00:00+09:00' \
  --evidence-url 'https://main.un.org/securitycouncil/en/content/un-sc-consolidated-list' \
  --note '公式原本・PRN差分・別名品質・掲載終了候補を確認した結果'

python -m src.un_review apply --event-id EVENT_ID --source-hash SOURCE_SHA256 \
  --expected-master-hash MASTER_SHA256 --operator '適用者名' \
  --at '2026-10-27T15:05:00+09:00' --note '承認済snapshotを反映'
```

90分以内にXML・全委員会を正常確認できた現在snapshotだけを承認・適用できる。監視エラー・範囲欠落・古いイベント・原本破損・master hash変化は停止する。Linux/macOSでは国連の監視・レビュー・適用を同じOSロックで排他し、適用直前にも国連世代hashを再確認する。既存の他ソースのローカル実行はこのロックに参加しないため、GitHub Actionsの定期実行と手元操作、複数ソースの手元操作は同じ作業ツリーで同時に行わない。

国連のStrong名称を全PRNから集約して反映する。同名の別PRNや別出典が有効なら維持する。国連由来だけが消えた行は承認後に国連出典を外し、他出典が無い場合のみ無効化して履歴を残す。初回master追加は`初回同期（国連）`と表示する。

適用コマンドはmaster、名簿検索gzip、一覧、差分、変更履歴、適用監査を同一世代で保存する。保存中の捕捉可能なI/O例外にはrollbackする。電源断・SIGKILLを跨ぐ完全原子性は保証しない。

master適用データは作業ブランチから別PRで公開する。OFAC・財務省の公開前監査も実行し、既存の監視データを取り込んだ最新mainと比較する。Excel/Release/Driveへの配布は既存の配布手順で生成する。定期国連監視は未承認の名簿を配布しない。

## Google Sheets導入順序

1. コードのPRを検証してmainに統合後、`watch-un.yml`を実行する。
2. 公式XMLと全14委員会の取得結果、`data/dashboard/un_reviews.csv`が公開されたことを確認する。
3. 稼働GASを退避し、`apps_script/Code.gs`と`apps_script/Mofa.gs`を最新版に置換、`apps_script/Un.gs`を追加する。
4. 手動同期して、8系統の監視状況、`11_国連レビュー`、既存の差分・外務省資料を確認する。

国連追加で監視状況は9～16行になる。直近変更を22～25行へ移し、重ならないようにする。11のメモは表示上の記録であり、CLIの承認とは別。

通知は既存`SLACK_WEBHOOK_URL`が設定されている場合だけ配信する。委員会発表検知、XML検証済候補、監視エラーを分けて通知する。未設定の場合はスキップをログに残す。

## Read-only transport diagnosis

`python -m src.un_transport_probe --out dist/un_transport_probe.json` compares six official endpoints: XML, update-history page, one committee index, the consolidated-list RSS, the press homepage and its separate RSS, using the same project-identifying collector and redirect policy. The `probe-un-transport` workflow runs the same command in GitHub and retains JSON evidence for30 days. It has read-only repository permissions and a separate CI concurrency group.

The report contains bounded public HTTP metadata, byte counts and content hashes. Cookies, authorization, raw document bodies and temporary signature queries are excluded. An explicit challenge header is distinguished from an unexplained empty202. A diagnostic job's success means observation collection completed; `HTTP200_UNVALIDATED` is not sanctions validation or a recovery of production source health.

The official homepage's current RSS URL is `https://main.un.org/securitycouncil/en/feed/1.0/updates_unsc_consolidated_list`. Observed items repeat the same page/GUID with publication labels and do not specify target IDs or actions. The diagnostic reports structural counts only. It cannot approve, delist or modify a master record, and does not clear HTML retrieval errors.

The separate official press homepage `https://press.un.org/en` publishes `https://press.un.org/en/rss.xml`. Its observed ten items have distinct article GUIDs, but include unrelated UN news and only a bounded recent window. Structural RSS success does not establish sanctions coverage, article authenticity, or recovery of the fourteen committee indexes. Both RSS responses use the same safe parser; challenges and malformed/unsafe XML remain explicit observations.

A response entering the UN document reader with explicit `x-amzn-waf-action: challenge` or `captcha` is recorded as `UNAccessChallengeError` before parsing/archiving; HTTP errors rejected earlier by the collector keep their existing HTTP-error handling. HTTP202 alone remains a generic transport failure; a healthy CloudFront response is accepted for the usual schema checks. GitHub evidence and RSS feasibility are recorded in `docs/evidence/un-transport-20261111/findings.md`.

Diagnostic output must be JSON outside business data/repository metadata directories. Symlinks and unrelated existing JSON are refused; a completed diagnostic report can be refreshed atomically. Exception text is reduced to controlled labels to avoid leaking signed relative request targets. Unsupported RSS encodings and unsafe declarations are reported without dropping the other observations.

## 公式報道RSSの補助監視

`watch-un.yml`の既存定期実行で、別の公式RSS `https://press.un.org/en/rss.xml`も取得する。
直近の国連全体の記事のみが入るため、14委員会一覧や更新履歴HTMLの代替にはしない。
RSS成功でも既存HTMLの取得失敗・監視範囲欠落は残る。タイトルによる候補判定には見逃しがあり、
全件網羅・制裁追加/削除・XML検証・master反映を示すものではない。

初回取得は基準として保存し、新着通知を出さない。以後、安保理の記事URLかつタイトルに
sanctions、asset freeze、arms embargo、travel ban等を含む新着・改訂を候補化する。
原本SHA256、URL、記事公表日時、初回検知日時、改訂前後を保持し、再取得で検知日時を変えない。
記事がRSSから消えても掲載終了とは判定しない。記事タイトルが関連外へ変わった場合も改訂候補を残す。

`data/un/state.json`の`press_rss`と`documents.press_rss`に独立保存する。
既存`data/dashboard/un_reviews.csv`と「11_国連レビュー」に補助監視概要・候補・取得/解析失敗・
未確認期間を表示する。CSVの11列構成は維持しているため、この機能のためのGAS差し替えは不要。
シートが既に国連レビューを同期している場合は通常同期で表示する。RSS候補は08_最新差分への確定名簿差分にはしない。

最新記事が72時間を超えて古い場合はSTALE、前回取得成功から90分超または前回記事との重複なしは
持続する未確認期間として表示する。これらやRSS取得失敗でも監視CLIは非ゼロ終了し、検証済み旧資料・候補は保持する。
成功した再取得だけで未確認期間を解除しない。72時間/90分は運用上の保守的な閾値であり、
その範囲内なら完全な監視だという保証ではない。既存webhookが未設定なら外部通知は配信されない。

状態と候補ID・原本hashの確認:

```bash
python -m src.un_review status
python -m src.un_review rss-review --event-id 'rss:EVENT_ID' --index-hash SOURCE_SHA256 \
  --reviewer '確認者名' --at '2026-11-11T10:00:00+09:00' \
  --note '公式記事本文とXMLへの反映状況を確認した結果'
python -m src.un_review rss-coverage-review --index-hash CURRENT_RSS_SHA256 \
  --reviewer '確認者名' --at '2026-11-11T10:00:00+09:00' \
  --note '未確認期間に対応する公式アーカイブの確認範囲と結果'
```

`EVENT_ID`/hash/日時は実際の値に置き換える。候補確認は原本hashに固定し、XML承認・master適用とは別。
範囲解除は直近90分以内に取得した、解除時点でも最新記事が72時間以内のRSS原本に限定する。
確認者・理由・元の欠落記録・解除時原本hashを残す。シートのメモだけでは解除されない。

候補確認時は保存済みRSSを再解析し、候補のタイトル・URL・GUID・公表日時・候補区分が
原本と一致すること、およびイベントIDの整合性を確認する。改訂候補は変更前原本のSHA256も保持し、
変更前レコードがその原本に存在し、同じ記事URLに属することを検証する。
関連候補の改訂では、変更前レコードと保持中の直前イベントの対応も検証し、
別の古い版や変更のない同一内容へのすり替わりを受け付けない。
旧形式で前版hashが無い場合は、保存済みの親イベントと変更前レコードが完全一致する場合だけ補完する。
前版根拠を特定できない場合は確認保留のまま停止し、推測で確認済みにしない。
未確認期間の解除でも原本のレコード一覧と最新公表日時を再照合する。
原本・保存状態に不一致があれば状態やレビューCSVを書き換えず停止する。確認処理は再取得や名簿更新を行わない。
