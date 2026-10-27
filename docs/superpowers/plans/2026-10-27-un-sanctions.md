# UN Sanctions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add independently auditable UN XML and committee notice monitoring with snapshot-bound review before master changes.

**Architecture:** A dedicated UN bundle owns validated snapshots, events and review state. Existing fetch, persistence, audit, heartbeat, master and dashboard helpers are reused; existing source transactions stay separate.

**Tech Stack:** Python 3.12, requests, standard library XML/HTML parsers, unittest, GitHub Actions, Apps Script.

**Spec:** docs/superpowers/specs/2026-10-27-un-sanctions-design.md

## Global Constraints

- Low-quality aliases never become UN screening inputs.
- Master application requires current snapshot hash, reviewer, timestamp and explicit apply.
- Failed fetch/schema checks preserve the last valid snapshot and master.
- UN delisting changes only UN provenance; rows and other sources remain.
- Initial population is baseline, not new official designation.
- Ships are outside the consolidated person/entity scope.

## Review Focus

- 304 with missing or corrupted cache must fail, not silently skip validation.
- Unknown alias quality, unknown regime and duplicate IDs must block the snapshot.
- A→B→A and pending reviews must retain event identity without reusing approvals.
- Notice detection independent of XML must stay visibly pending until reviewed.
- Rebased publication must not overwrite another source's outputs.

### Task 1: Parse official data

**Files:** Create src/un_sources.py, tests/test_un_sources.py.
**Interfaces:** parse_xml(body: bytes) -> dict; parse_notices(body: bytes, url: str) -> list[dict]; validate_un_url(url: str) -> str.
- [x] Write tests for primary/original/Good/Low/f.k.a., metadata changes, malformed/empty/duplicate/unknown XML, actual official fixtures and off-domain redirects.
- [x] Run `python -m unittest tests.test_un_sources` and observe missing parser failure.
- [x] Implement parsing with strict structure, stable IDs, canonical fingerprints and explicit committee coverage.
- [x] Run the same command; expect PASS; commit parser and tests.

### Task 2: Monitor and record

**Files:** Create src/un_watch.py, tests/test_un_watch.py; modify src/dashboard.py, src/source_audit.py.
**Interfaces:** run(root: Path, *, fetcher=fetch, now=None) -> dict; data/un/state.json and snapshot.json; data/dashboard/un_reviews.csv.
- [x] Write tests for baseline→304→amendment→304, cache corruption, low alias, pending notice, schema/HTTP failure and transaction rollback.
- [x] Run `python -m unittest tests.test_un_watch` and observe missing watcher failure.
- [x] Implement bounded official fetches, immutable raw archives, diffs, review queue and atomic success/error publication. Store notice baseline and same-URL amendments independently.
- [x] Run parser/watcher tests and full `python -m unittest discover -s tests`; expect PASS; commit.

### Task 3: Review, delivery and operations

**Files:** Create src/un_review.py, tests/test_un_review.py, .github/workflows/watch-un.yml, docs/un-sanctions.md; modify apps_script/Code.gs and README.md.
**Interfaces:** `python -m src.un_review approve/apply/status/notice-review`; application requires hash and current master hash. Approvals identify a snapshot event, not just a historical hash.
- [x] Write tests for unapproved/stale apply, strong-only import, cross-source and same-name party preservation, alias removal, and notice review.
- [x] Run `python -m unittest tests.test_un_review` and observe missing reviewer failure.
- [x] Implement snapshot-bound review/application with atomic audit and master/output projection; add scheduled workflow and source labels.
- [x] Run complete Python suite, existing offline scripts, Node suites and `git diff --check`; expect PASS.
- [x] Run the official XML and all committee index acquisition in an isolated temporary root. Review the real counts and failure states; keep production master unchanged until review.
- [ ] Commit, open PR and inspect CI. Publish only the verified branch; do not merge without user authorization.
