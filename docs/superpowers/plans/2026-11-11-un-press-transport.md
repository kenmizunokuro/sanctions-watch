# Official UN press transport implementation plan

> Implement inline using superpowers:executing-plans, with test-first verification.

**Goal:** Compare the official UN press publisher with the blocked committee publisher.
**Architecture:** Extend only the existing read-only diagnostic and its fixture corpus.
**Tech Stack:** Python 3.12, requests, unittest, existing GitHub Actions.
**Spec:** `docs/superpowers/specs/2026-11-11-un-press-transport-design.md`

## Global constraints

Six configured targets; existing network policy and safe output contract; no financial writes.

## Review focus

Challenge responses with valid-looking RSS must never be parsed. An independent failure
must not suppress other targets. Global press RSS cannot prove committee coverage.
Existing callers and CLI output protection must remain compatible. New metadata must
not leak cookies, signed query strings or article bodies.

### Task 1: Extend the diagnostic comparison

- [x] Add fixtures and tests for the two observed official press endpoints, independent
  failures and challenge handling; run new tests and observe missing-target failures.
- [x] Add `PRESS_HOME_URL`, `PRESS_RSS_URL` and two entries in `TARGETS`; use `_rss_shape`
  for both RSS source IDs. Preserve the existing report schema and classifications.
- [x] Run transport/source/watch tests, offline checks and `git diff --check`.
- [x] Document official discovery, local observations, scope and full validation results.
- [x] Review, commit and publish a PR; inspect GitHub checks and the diagnostic artifact.
- [x] Record measured GitHub feasibility without declaring blocked sources healthy.

## Execution record

Implemented inline in a fresh dedicated checkout on `feat/un-press-transport-20261111`.
Three tests failed on the absent targets, then all 14 diagnostic tests and 50 related
tests passed. Full local regression: 831/831; offline: 213/213. Independent reviewer:
no Critical/Important findings. PR65 GitHub diagnostic42930184150 verified the two
press endpoints as HTTP200 while the original HTML/RSS remained explicit challenges.
Downloaded artifact13815563325 and verified its ZIP SHA256 before retaining the JSON.
No production monitor, financial state, source health or master logic was changed.
