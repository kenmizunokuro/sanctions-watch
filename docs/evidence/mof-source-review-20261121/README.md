# 財務省 2026-11-20 原本更新のレビューと反映

状態: **利用者承認済み・反映実施（2026-11-21T13:42:27Z）。**

承認指示は「全部すすめてくれ」。承認した4行と原本SHA256は `data/review/approved_mof_source_update_20261121.json` に固定し、適用記録を `data/monitoring/approved_mof_source_update_20261121_applied.json` に保存した。

## 原本差分

| 項目 | 件数 |
| --- | ---: |
| 原本の人物・団体レコード | 2,897 → 2,896 |
| 原本レコード追加 | 0 |
| 原本レコード削除 | 1 |
| 情報改訂の対象者 | 0 |
| レビュー候補合計 | 1 |
| 財務省抽出名称件数 | 15,245 |

初回検知は **2026-11-20 06:02:50 JST**（`2026-11-19T21:02:50Z`、`actions/runs/44613539988`）。再取得・反映時刻と区別し、差分公開時もこの初回検知日時を維持する。

## 掲載終了（一次資料確認）

国連安保理1518委員会による `IQe.200`（`LOGARCHEO S.A.`）の制裁解除（2026-10-05）を受け、日本政府（外務省・財務省・経済産業省）が令和8年11月19日に発表・11月20日公布の外務省告示により、イラク前政権関係者等（平成22年外務省告示第342号 別表Ⅲ）から1団体を解除した。

| 原本番号 | 対象者 | 別名 | 今回反映内容 |
| --- | --- | --- | --- |
| `007-000200` | ロガチェオS.A. (`LOGARCHEO S.A.`) | ロガチェオ / `AGLOGARCHEO AG` | 財務省単独の3名称を無効化、OFAC SDN現役（Party `8214`）の `LOGARCHEO S.A.` はOFAC単独で有効継続 |

## マスター反映対象（全4行）

1. `ロガチェオs.a.` (`ロガチェオS.A.`): 財務省単独 → `status="無効"`（`first_seen_ms` 保持）
2. `ロガチェオ` (`ロガチェオ`): 財務省単独 → `status="無効"`（`first_seen_ms` 保持）
3. `aglogarcheoag` (`AGLOGARCHEO AG`): 財務省単独 → `status="無効"`（`first_seen_ms` 保持。公式PDF/CSV原文上の連結表記）
4. `logarcheos.a.` (`LOGARCHEO S.A.`): `OFAC;財務省` → `OFAC` / `OFAC:SDN`（`status="有効"` 継続。OFAC SDN Party `8214` が現役のため）

注: `logarcheoag` (`LOGARCHEO AG`) は既に `OFAC` 単独行のため変更なし。

## 一次資料・ハッシュ

- [財務省 2026-11-20 CSV](https://www.mof.go.jp/policy/international_policy/gaitame_kawase/gaitame/economic_sanctions/shisantouketsu20261120.csv)
  - SHA256: `01e6141505b7733ad2ff07a0acd312c11d7abb55bd57607db74390b195c2ce21`
- [財務省 報道発表（令和8年11月19日）](https://www.mof.go.jp/policy/international_policy/gaitame_kawase/gaitame/economic_sanctions/iraqhoudou_20261120.html)
  - SHA256: `565efdd5eb8912d76a5d96fd00f657bb016e03c8d81cfb34ea364ef6b6045f9c`
- [外務省・財務省・経済産業省 共同別紙（令和8年11月19日）](https://www.mof.go.jp/policy/international_policy/gaitame_kawase/gaitame/economic_sanctions/gaitamehou_shisantouketsu_20261120.html)
  - SHA256: `4c6a2d9cc3a9cb67f918e1df723c95443580924673a0dcb40c9ac215b8d0305b`
- [資産凍結等の措置の対象から解除された者（PDF）](https://www.mof.go.jp/policy/international_policy/gaitame_kawase/gaitame/economic_sanctions/iraq_kankeisha_20261120.pdf)
  - SHA256: `033cbc70dcb44ba7424b581d3f370e88aae4604ff679343175f6d622ade0dcff`
- [改訂後イラク関係者リスト（PDF）](https://www.mof.go.jp/policy/international_policy/gaitame_kawase/gaitame/economic_sanctions/iraq_list_20261120.pdf)
  - SHA256: `ea7ec398456f7e90686abf631c4d58596bab45935f2889883814d08ec04c5cf0`
