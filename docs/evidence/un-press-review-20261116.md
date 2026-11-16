# 国連の公式発表2件と統合XMLの照合

2026年11月16日。時刻はJST。取得原本と監査記録は、同日16:10に公開された [観測コミット](https://github.com/kenmizunokuro/sanctions-watch/commit/32766e6f81e370a2d240d665fc688b4974f4b081) と [本番実行 43821215282](https://github.com/kenmizunokuro/sanctions-watch/actions/runs/43821215282) に固定した。

| 発表 | 照合結果 | 原本 |
|---|---|---|
| SC/16506 | 1533委員会が11月11日に既掲載団体 `CDe.010`（ALLIANCE FLEUVE CONGO (AFC)）を改訂。統合XMLの前後比較では既存PRNに別名 `Front pour la Refondation de la Nation (FRN)` が追加され、11月15日生成のXMLにも残っている。 | [報道発表HTMLの保存原本](https://github.com/kenmizunokuro/sanctions-watch/blob/32766e6f81e370a2d240d665fc688b4974f4b081/data/raw/un/e145cf71f9c959b52c0ec398a64276b03b3b37222dfd02abfab1514c2c947429.html.gz) · [1533委員会の通知PDF](https://main.un.org/securitycouncil/sites/default/files/2026/1533_NV_En_Amd%201.pdf) |
| SC/16508 | 安保理が11月12日に2140制裁制度に関する決議2837を採択。取得した報道発表では渡航禁止・資産凍結の延長と専門家パネルの任期延長を確認。個別PRNの追加・掲載終了を示す根拠には使わない。 | [報道発表HTMLの保存原本](https://github.com/kenmizunokuro/sanctions-watch/blob/32766e6f81e370a2d240d665fc688b4974f4b081/data/raw/un/238ba7d1a0b7eba98a8aa71f9df6d1edfb7807c626b3269b13ea71eb1fc5aa6d.html.gz) · [安保理の決議一覧](https://main.un.org/securitycouncil/en/content/resolutions-0) |

## 恒久参照番号の不一致

国連の更新履歴では、SC/16506の行が1533委員会と `QDe.010` を併記している。保存済みの公式発表、委員会通知、統合XMLはいずれも `CDe.010` を示す。XMLで `QDe.010` は1267系の ISLAMIC MOVEMENT OF UZBEKISTAN を指し、比較した3世代のいずれでも名称と別名 `IMU` は同じだった。この不一致を根拠に `QDe.010` を更新したり、更新履歴の番号を自動補正したりしない。[国連の更新履歴](https://main.un.org/securitycouncil/en/content/list-updates-unsc-consolidated-list)

XML原本はgzipを展開し、SHA256がファイル名と一致することを確認してから `REFERENCE_NUMBER` で抽出した。次の3ファイルで再現できる。

- 改訂前。[`68c062b4…`](https://github.com/kenmizunokuro/sanctions-watch/blob/32766e6f81e370a2d240d665fc688b4974f4b081/data/raw/un/68c062b4fd6a3e98ffe63499c80da78875acdd817170c4abc198e2a11a214e59.xml.gz)
- 改訂検知時。[`4c56e0ca…`](https://github.com/kenmizunokuro/sanctions-watch/blob/32766e6f81e370a2d240d665fc688b4974f4b081/data/raw/un/4c56e0ca1ef09f9c436f4fcadd29916d1ed577b2b85134ea988d4817fabff5a7.xml.gz)
- 11月15日生成。[`2a0390e9…`](https://github.com/kenmizunokuro/sanctions-watch/blob/32766e6f81e370a2d240d665fc688b4974f4b081/data/raw/un/2a0390e99d71ec03f2a065529aa0023ce8934e87e0115b6c755ab372ee1ef245.xml.gz)

## 監視と表示の状態

- 16:10の本番実行は、更新履歴1ページと委員会発表一覧14ページのすべてでHTTP 202および `x-amzn-waf-action: challenge` を記録した。統合XMLはHTTP 304で保存済み原本を再検証し、2件の記事原本はHTTP 200で新規取得・保存した。RSSの未確認期間は継続している。[本番実行](https://github.com/kenmizunokuro/sanctions-watch/actions/runs/43821215282)
- 17:35の確認時点で直近のスケジュール起動は10:58。16:10の観測はmainへの変更から起動した実行による。定期起動の遅延と、起動後の公式HTML取得失敗はそれぞれ確認が必要。[定期起動の履歴](https://github.com/kenmizunokuro/sanctions-watch/actions/workflows/watch-un.yml?query=event%3Aschedule)
- 公開レビューCSVには2件分の `EVIDENCE_READY` 行があり、元のRSS候補は `PENDING_REVIEW`。SC/16506の検知日時は11月12日11:38、SC/16508の復元日時は11月16日15:04。会社のGoogle Sheetsは接続中のアカウントで閲覧できず、同期未確認。この照合でレビュー承認やmaster適用は行っていない。[公開レビューCSV](https://github.com/kenmizunokuro/sanctions-watch/blob/32766e6f81e370a2d240d665fc688b4974f4b081/data/dashboard/un_reviews.csv)
- 監視範囲を別経路で補う場合は、国連統合制裁リストの公式配信登録フォームを使う。配信本文の保持と既存のRSS・XML照合は別途必要。配信の受信実績や対象範囲はこの調査では検証していない。[国連の配信登録](https://main.un.org/securitycouncil/en/content/un-sc-consolidated-list)
