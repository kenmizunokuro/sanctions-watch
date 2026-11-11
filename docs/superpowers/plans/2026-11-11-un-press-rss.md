# UN press RSS implementation plan

> Implement inline with test-driven-development and independent final code review.

**Goal:** Persist auxiliary RSS candidates during the existing UN scheduled monitor.
**Architecture:** A pure RSS parser/event observer plus existing atomic UN publication.
**Tech Stack:** Python 3.12, unittest, requests, existing GitHub Actions and review CSV.
**Spec:** `docs/superpowers/specs/2026-11-11-un-press-rss-design.md`

## Global constraints

No master writes, no primary-source error clearing, no inferred sanctions actions.
RSS publication freshness 72h; maximum poll gap 90m. Existing CSV11 schema unchanged.

## Review focus

DTD/UTF16, duplicate fields/identity, malicious URLs, title reversion, no overlap,
stale cached feed/304, first-detection retention, failed fetch preserving state,
publication rollback, primary HTML failures, hash-bound manual review and no master writes.

### Task 1: Parser and pure observer

- [x] Test rejection, baseline/new/revised/reverted/unrelated items, freshness and gap
  persistence, then implement `parse(body, now)` and `observe(previous, items, source_hash, now)`
  in `src/un_press_rss.py`. Run unit tests and observe RED→GREEN.

### Task 2: Atomic monitor and reviewer integration

- [x] Test and add RSS collection to `src/un_watch.py`, summary/candidate/failure/gap
  rows, CLI outputs and optional workflow notices. Update shared fixture for the new URL.
- [x] Test and add hash-bound `rss-review` / `rss-coverage-review` in `src/un_review.py`.
- [ ] Run related tests, full regression, offline checks and meaningful live isolated
  monitor verification. Check master/primary error preservation and actual baseline.
- [ ] Independent review, fix important findings test-first, commit one complete PR,
  inspect all CI, merge within authorized scope and verify production published state.

## Execution ledger

Baseline: 50 related UN tests passed on PR65 main. Isolated linked worktree created.
Parser produces list[dict] consumed by observer; monitor publishes observer dict in
state.press_rss and metadata in documents.press_rss. Reviewer consumes that same state.
Parser/observer RED6→GREEN; integration RED5→GREEN. Related65 Python +3 GAS passed.
Independent reviewer reported freshness-at-review and CSV/CLI identifier mismatch;
corrected with freshness and displayed-ID tests, plus status CLI exposure. Execution
and full suite/CI/main completion results are retained in the PR to avoid a second
code-identical CI run solely for recording its own successful outcome.
