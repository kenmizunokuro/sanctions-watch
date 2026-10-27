# 未確認旧登録の期限付き保持 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Implement inline on existing isolated feature worktree. User approved the PR implementation policy with “OK”; production activation, master migration, ordinary7475 retention and key merge remain unapproved.

**Goal:** 固定2429件を未確認表示のまま30日以内の個別ID承認で継続保持できる仕組みと候補データを実装する。

**Architecture:** 固定scopepack＋専用activation ledgerを検証する。master候補へ既存列の警告を付け、同じ語・有効状態・高リスク・元出典を保持。監査は公開資料の警告まで検証してからREVIEWへ移す。既存7475保持承認と重大不整合のvetoは独立に残す。

**Tech Stack:** Python標準ライブラリ/unittest、既存CSV/gzip/Excel/実bashゲート。

**Spec:** `docs/source-integrity-legacy-retention-policy-proposal.md`（policy implementation approved2026-10-26; activation remains pending）

## Global Constraints

固定2429件のみ、期間30日以下、変更/期限切れは失効、公式名/Alias欠落・現行ID/状態不整合・取得/解析/hash/証跡失敗は免除不可。現master/state/raw/Alias履歴・会社Apps Script変更なし。承認台帳はDRAFTを候補として生成し、現data/reviewへACTIVEを設置しない。削除/解除・Phase2・mergeなし。

## Review Focus

scope偽装/未承認コピー、注記を失う出力、期限境界と30日超過、原本関係が変わった旧対象、通常保持との相互免除。実動作テストで各境界を確認する。

## Task1: exact scope, annotation, candidate preparation

Create `src/integrity_legacy.py`, `tests/test_integrity_legacy.py`; modify `src/integrity_triage.py` to expose an unchanged semantic review-ID helper.
Interfaces: `load_scope(root)->dict`; `annotate(row,classification,expires_at)->dict`; `prepare(rows,scope,reviewed_at,expires_at)->(rows,draft_ledger)`; row-bound review IDs use frozen facts. Default scopepack/proposal bodies are hash-pinned.

- [x] RED tests: valid actual scope, tampering/outside entry rejection, nonmutating warning annotations, exact prepared IDs, DRAFT cannot activate,30day boundary.
- [x] Implement minimal policy and preparation CLI with exclusive outputs outside active data/. Preserve existing review IDs for unmodified rows.

## Task2: gate and display/export integration

Modify `src/integrity_audit.py`, `src/integrity_triage.py`, `src/export_excel.py`, `src/export_diff_excel.py`; add relevant real gate/export tests.
Interfaces: `apply(root,report,triage,ledger,scope,now)->report copy`; `validate_for_export(root,rows,now)`; activation path `data/review/integrity_legacy_acceptances.json`.

- [x] RED→GREEN: exact accepted row+relation+warnings permits REVIEW; master/report/proof change, expiry, missing/invalid list/screening display and mandatory failures veto with evidence. Ordinary retention cannot accept legacy and vice versa.
- [x] Excel remains7columns, list4columns, screening9columns. Test real warning values and no designation/delisting events from preparation.

## Task3: whole validation, simulations and delivery

- [x] Fresh fullsuite/offline. Prepare2429 annotated candidate rows in scratch only; real-source audit with temporary activation shows2429 accepted and7477 original blockers remain. Separate hypothetical full-retention/key-merge fixture proves REVIEW only with all approvals, warnings and valid sources.
- [x] Repeat actual JSON, corrupt/expired simulations, old evidence and business data unchanged. Independently review final increment; fix Important with RED→GREEN and green whole suite.
- [ ] Commit candidate/draft/simulation evidence under docs, update PR, verify remote tree/CI, report production approval still pending.

Ruling: Existing4column Apps Script search has no review flag. Prefix the existing risk_type value with a legacy warning in candidate rows, in addition to review_flag/remark, so unmodified Apps Script displays it. Preserve original risk_type in immutable scope and every other business field. Cost: metadata migration needs explicit production approval; it must not emit legal designation/delisting events.

Review outcome: Independent reviewer reproduced two stale unmarked projection bypasses. Added RED real-gate regressions and fixed both through exact full Counter comparison. Independent rerun confirmed rejection and no remaining Critical/Important findings.599 tests pass. Actual offline official-source scenario:2429 accepted alone leaves7477 blockers; all separate hypotheses yieldREVIEW/0 blockers. Production unchanged.
