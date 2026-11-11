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

- [ ] Add fixtures and tests for the two observed official press endpoints, independent
  failures and challenge handling; run new tests and observe missing-target failures.
- [ ] Add `PRESS_HOME_URL`, `PRESS_RSS_URL` and two entries in `TARGETS`; use `_rss_shape`
  for both RSS source IDs. Preserve the existing report schema and classifications.
- [ ] Run transport/source/watch tests, offline checks and `git diff --check`.
- [ ] Document official discovery, local observations, scope and full validation results.
- [ ] Review, commit and publish a PR; inspect GitHub checks and the diagnostic artifact.
- [ ] Record measured GitHub feasibility without declaring blocked sources healthy.
