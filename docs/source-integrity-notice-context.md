# Phase 1 過去の公式発表と未確認名称の文脈追跡

2026年10月26日（日本時間）。前回の出典未確認1,633件から147件に過去のOFAC公式発表での言及根拠を追加し、未確認は1,486件となった。147件を掲載名として確認できた、現在制裁対象である、解除すべきである、という結論ではない。追加判断対象は2,429件のまま、保持候補7,475件は全て未承認、公開判定もBLOCKEDを維持する。

## 回収した原本

既存のfetchで公式URLへ通常のGETを行い、両方HTTP 200、最終URLは指定URLと同一だった。アクセス拒否の回避や経産省・外務省への接続はしていない。本文はそのままgzipで保存し、URL・本文SHA256・取得日を既存の `docs/evidence/integrity-notices/manifest.json` へ追加した。20250630の原本・既存manifest行は不変。

| 公式発表 | 追加で言及が見つかった未確認名称 | 本文SHA256 |
| --- | ---: | --- |
| https://ofac.treasury.gov/recent-actions/20260727 | 138 | `feb7f91b4ac027cdb123fa8a2b4033410a21fc5a00dbd09967de77e6d62919b3` |
| https://ofac.treasury.gov/recent-actions/20251218 | 9 | `58a55b051f58ac9f684c3f2d70460e97ffd7acf230005362bf0e89dd1429ea34` |

名称検索から追跡した2ページであり、OFACの全履歴を網羅する収集ではない。主名称・別名・関連先等の言及が混在するので、既存triageでは全て `NOTICE_MENTION_ONLY`、current=false、source_id=None、保持承認対象外の補助根拠として扱う。現在のSDN/Consolidated原本は再取得せず、前回のhash固定保存版を使っている。

## 具体的に分かった違い

- **365 DAYS FREIGHT SERVICES FZCO**：20251218発表のSDN List / “The following deletions…”の欄に、同名で始まるレコードがある。
- **A.T.E. INTERNATIONAL LTD.**：20260727発表の同欄に、同名で始まるレコードとRWR INTERNATIONAL COMMODITIESのa.k.a.欄での言及がある。
- **BUGAYENKO, Dmitry Vitalyevich**：20251218発表で5レコードに言及があるが、今回の一致箇所はVELES INTERNATIONAL LIMITED等の **Linked To欄**。削除見出しの下に文字列があることだけで、この人物の削除レコードだと判断してはいけない。

見出しは発表当時の操作を説明するもの。現在の掲載状態、masterの元登録対象との同一性、保持の意図を独立確認したものではない。旧masterの出典ラベルや同名一致だけで解除・誤登録を断定しない。

## 読取専用の文脈レポート

`src/integrity_notice_context.py` を追加した。取込パーサや既存の本文言及パーサに依存せず、標準HTMLParserで本文領域だけを読み、節見出し・小見出し・段落/二重brで区切った全テキスト・tableの全行・一致文字位置を記録する。単一brとタグ間空白は単語をつなげない。tableセル内の段落や二重brがあっても、関連先やUIDを含む行全体を残す。表のUIDは文字列のままで、source_idとして解釈しない。

本文領域の欠落/重複/未閉鎖、未知要素、不正な親子構造、入れ子段落、不正void終了タグ、表外td等は失敗として終了非0になり、部分レポートを出さない。原本hash/領域外パス/非公式URL/重複原本/固定baseline相違も拒否する。未知見出しはそのまま残し、actionへ分類しない。

この補助レポートは **RESEARCH_ONLY**。公開ゲートには使わず、保持判断を免除しない。固定baselineは10月24日のperson-triage（JSON本文SHA256 `737760c156f89a7f6275785d5c20d2596368590c012d59332163e0e221381c69`）。通常は変更を拒否する。`--baseline` と `--baseline-sha256` の明示指定は管理された別入力の研究・fixture用であり、baseline hashを結果に残す。別入力を10月24日の固定実験と称するための機能ではない。

対象2,429件のうち **907件に合計3,152箇所**の文脈が付いた（既存760＋今回147）。一致しない1,522件も空欄のままレポートに残す。未解決の名前が全て原文の独立した掲載欄にあるという意味ではない。各節の全テキストレコード数は20250630が1,053、20260727が373、20251218が68で、掲載対象者数ではない。

## 実データ監査と候補ID

master89,341行・必須公式名称108,836件・検出事象9,914件・ブロック事象9,906件は不変。基本監査の名称/Alias欠落、Party ID不一致、現行原本解析失敗はこの保存入力では0で、残る不整合を正常扱いしていない。

| 項目 | 前回 | 今回 |
| --- | ---: | ---: |
| 出典未確認 | 1,633 | 1,486 |
| 過去公式発表の言及のみ | 760 | 907 |
| 非名称欄のみ | 33 | 33 |
| Weak/Strong別Party衝突 | 3 | 3 |
| 追加資料・判断対象の合計 | 2,429 | 2,429 |
| 保持レビュー候補 | 7,475 | 7,475 |
| 保持承認 | 0 | 0 |

180事象に追加根拠が付き、review_idは177件変わった（うち保持候補30件）。根拠のsemantic factsが既存と同じ3件はID不変だが、原本を含むevidence hashは変わる。旧候補・旧証跡は削除せず、今後は今回のID/根拠でレビューする。新しい候補ファイルも全件PENDINGで、承認台帳へ自動移植しない。

## 変更ファイル・保存証跡・再現

- `src/integrity_notice_context.py` / `tests/test_integrity_notice_context.py`：補助読取・実CLIの失敗/排他出力テスト。
- `docs/evidence/integrity-notices/ofac-{20260727,20251218}.html.gz` / `manifest.json`：公式原本2件と出典。
- `docs/evidence/source-integrity-20261026-notice-{audit,triage}.json.gz` / `notice-triage.csv.gz`：実際の全件監査結果。
- 同prefixの`context.json.gz`：全2,429件の文脈と原本位置。`summary.json` / `transitions.json`：全分類と180件の追加根拠・ID差分。
- 同prefixの`retention-review-groups.json` / `evidence-needed.csv.gz`：PENDING7,475件、追加判断2,429件。

```bash
python -m unittest
python -m tests.test_offline
python -m src.integrity_audit --sources all --triage
python -m src.integrity_notice_context --out /tmp/notice-context-new.json.gz
```

全unittest **566/566成功**、既存offline **213/213成功**。監査コマンドの終了1/BLOCKEDが期待結果で、文脈コマンドの終了0は研究レポートを作成できたという意味だけである。監査・triageを2回、最終版の文脈レポートを2回実行して、それぞれJSONの完全一致を確認。出力先は存在しないファイルを指定し、上書きは拒否する。履歴回収の旧実験はこれまでどおり当時のcommitで実行し、追加原本を3原本回収の効果へ混ぜない。

独立レビューで見つかった表行の部分分割と不正HTML構造の成功を、失敗するテストで再現して修正した。新規14件は実データ形のHTMLと実CLIを使い、mockパーサ同士の比較はしていない。

## 残る限界と判断

軽微な未対応は、見出し内部のbrの区切り空白が失われる表示である（例：SDN＋br＋ListがSDNList）。今回の3原本の見出しにはこの形が無く、本文レコードや保持判定へ影響しない。該当する将来資料では見出し表記の再確認が必要。

この追補は資料の文脈を増やしたもので、同一性・現在掲載・保持・解除の判断はしない。未確認1,486件の追加原本、言及907件の掲載/関連先関係、非名称欄33件、品質衝突3件の確認は継続課題。既存キー問題と財務省ID独立列不足も未解決。本番master/state/raw/Alias履歴、会社Apps Script、既存原本・監査証跡は変更していない。自動削除/解除、Phase 2、本番反映、mergeは実施していない。
