# Phase 1 一次ソース完全性監査

目的: 取得成功・更新差分とは別に、公式原本からmaster候補とParty/Alias履歴を検証し、漏れ・余分な出典・誤生成・ID不整合・解析不能を検出する。

調査基点: main ea4ac7f0319ea3ea285d8d26c77be1fd17e92cda。
既存: source_auditはHTTP/ハッシュ台帳、mof_record_diffは原本間差分、OFACはClassic/Advanced Party集合照合・Alias履歴・承認制掲載終了。masterは名前キー単位で複数出典を統合し、IDを保持しない。

方式比較: 既存パーサ再実行だけでは共通バグを検出できない。別ネットワーク取得は世代差と負荷を増やす。採用する方式は取得済みのハッシュ固定原本を独立リーダーで読む後段監査。OFACはSAX（本体はElementTree）、Classic CSVとXMLを独立に照合。財務省は独立した厳密CSV/セル解析。match_keyのみ既存masterとの接続契約として共用する。

公開境界: run-watchの取得後、Excel/commit/release/通知前で必須ゲート。master候補はローカル生成済みでも外部には未公開。不整合・原本不在・hash不一致・解析不能は非ゼロ終了。取得失敗時も監査を実行し、最終結果を取得正常と誤認させない。失敗証跡はActions Artifactに保存。監査自身はmaster/state/index/既存台帳を変更しない。

検証: 主名称/別名/Weak、掲載状態、出典/category、OFAC (list, Party ID, name)と現在フラグ・品質を原本で検証。弱い別名はOFACでは履歴必須、通常master登録必須にはしない。財務省Weakの扱いは既存方針を変更しない。掲載終了や原本に無い登録はレビューとして公開を止めるだけで削除しない。経産省・外務省・暗号資産・Apps Scriptは変更しない。

証跡: 原本URL/hash/path、master/index/stateのhash、公式レコードの番号/Party ID/列/行/原文、検出分類、master該当行をJSONに保存。成功・失敗ともrunごとに別ファイル、入力固定時の監査内容は決定的。MOF IDは原本から再構成した対応であり、masterに独立なIDが無い限界を明記する。元パーサとの一致だけで完全性を宣言しない。

対象外: Phase 2、法的解除判断、自動削除、本番反映、アクセス制限回避、財務省の自由文全文に含まれる暗黙の別名の推測抽出。
