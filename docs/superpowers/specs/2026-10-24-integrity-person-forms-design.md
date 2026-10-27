# Phase 1: 明示された姓名フィールドの監査根拠

継続指示に基づく未確認登録の原因調査。原本に名称の部品があるが、表記順の違いで旧masterとの根拠関係が見えていない事例を補う。本番master・既存キー・承認ファイルの変更、Phase 2は含めない。

## 原因と採用する方法

実例：Party39585の弱別名はXMLでLast Name=Peijnenburg、First Name=Aamと明示されているが、既存リーダーの物理順連結はPeijnenburg Aam、旧行はAam Peijnenburg。Party28545のKADKHODAEE, Abbas Aliでは、共有正規化の法人接尾辞AB前方一致によって姓名順を変換しない。旧行のキーを一括変更すると既存master/index/他ソースの照合にも影響する。

1. グローバル正規化を修正し全キーを再採番：本番データの移行判断が必要なため今回採用しない。
2. 全ての単語順を推測して照合：同名別人・団体を誤結合するため採用しない。
3. XMLの明示メタデータを独立SAXリーダーで読み、IndividualのFirst/Middle/Last Nameだけから表示順候補を作る：今回の実装者判断。原本に無い追加Aliasとは扱わず、保持レビュー候補の根拠としてだけ使う。

## 境界

- `src/integrity_person_forms.py`: オフライン、I/Oなし。原本のPartyType/SubType、NamePartType、IdentityごとのNamePartGroupを確認し、同一DocumentedName・同一Scriptの1個のFirst Name、0〜1個のMiddle Name、1個のLast Nameを並べる。
- Entity、False Identity、複数scriptの混合、曖昧な重複部品、未対応部品から候補を推測しない。XMLの構造/文字コード/namespace/version/真偽値失敗は例外として停止。表示候補を構成できない資料の件数と理由もmanifestに残す。
- 現行Advanced原本だけを追加対象にし、主名称/別名・弱品質、Party ID、script、元部品、URL/hash/位置を保持する。歴史資料をcurrentへ昇格しない。
- `src/integrity_triage.py`: 既存の原本文字列との照合を維持し、EXTRAの説明にCURRENT_PERSON_ORDER_VARIANT / CURRENT_WEAK_PERSON_ORDER_VARIANTを追加する。自動承認しない。強い別Party/名称の根拠が衝突するWeak-only問題はWEAK_EVIDENCE_CONFLICTで停止する。
- review_idに明示された部品型と値も含める。全てのParty関係を保存し、似た名前から特定Partyへの同一性を断定しない。
- 初回と前回の証跡を残して今回の報告を別名保存する。過去回収実験はパーサの意味的baselineも照合し、将来コードの変更効果を3原本回収の効果に混ぜない。

新しい表示候補はスクリーニング名簿へ追加しない。共有match_keyのAB前方一致問題は、今回の証跡によって影響を示すだけで、再採番を伴う修正は未実施と明示する。
