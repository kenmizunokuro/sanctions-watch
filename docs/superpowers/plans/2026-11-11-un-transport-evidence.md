# UN Transport Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Obtain safe, reproducible HTTP evidence for4 official UN targets in GitHub before treating RSS as a monitoring source.

**Architecture:** A pure collector uses the existing fetch and URL policy, then reports bounded safe metadata and optional RSS structural statistics. A CLI writes JSON; a separate read-only workflow runs it without the financial writer lock.

**Tech Stack:** Python3.12, existing requests/fetch, standard-library XML/JSON, GitHub Actions.

**Spec:** docs/superpowers/specs/2026-11-11-un-transport-evidence-design.md

## Global Constraints

Exactly4 official targets; existing project-identifying collector/UN validator; conditional requests disabled;15-second request timeout;32MiB bound; no challenge retry/solving or identity changes. No business-state writes. Signature queries, credentials, cookies and raw bodies never appear in reports. HTTP200 is explicitly unvalidated transport evidence. RSS is not a legal change source.

## Review Focus

- HTTP202 without a challenge header must not be attributed to an access-control vendor.
- Signed XML redirects and exceptions must not leak temporary tokens.
- A failing target must not suppress the other observations.
-200 with challenge/unsafe XML must not be described as a valid RSS source.
- CLI/workflow outputs must not mutate financial state or claim recovery from diagnostic success.

### Task1: Read-only evidence collector and CLI

**Files:** Create src/un_transport_probe.py; tests/test_un_transport_probe.py.

**Interfaces:** probe(*,fetcher=fetch,now=None)->dict, main(argv=None,*,fetcher=fetch)->int. Pure probe returns schema_version, checked_at, observations and probe_completed. CLI writes the report to --out(default dist/un_transport_probe.json).

- [x] Write failing tests for explicit challenge vs empty202, HTTP errors/timeouts with continuing targets, safe URL/header redaction, honest body hash and unvalidated200, unsafe/valid RSS shape statistics, and CLI preservation of business files.
- [x] Run tests and verify the implementation-missing failure is the expected RED.
- [x] Implement the finite collector and CLI using existing fetch/url redaction rather than another HTTP client.
- [x] Run the focused tests plus tests.test_un_sources,tests.test_un_watch,tests.test_un_review,tests.test_fetch_retries,tests.test_source_audit_url_safety. Expected: all pass.
- [x] Commit implementation and tests.

### Task2: GitHub evidence and final verification

**Files:** Create .github/workflows/probe-un-transport.yml; update docs/un-sanctions.md and a curated diagnostic evidence file after the real run.

**Interfaces:** python -m src.un_transport_probe --out dist/un_transport_probe.json. JSON artifact transport-un-RUN-ATTEMPT, retention30 days. Existing mandatory full/offline CI is retained.

- [x] Add a read-only workflow for matching PR/main changes and manual runs.10-minute job timeout; own concurrency group and latest-CI cancellation; artifact retained even when a source fails.
- [x] Parse YAML/embedded Python and git diff --check. Run the full Python suite and offline checks. Expected: all pass; no business data diff.
- [x] Request one independent whole-branch review; address Important/Critical defects by RED→GREEN tests and a green suite.
- [x] Publish a review branch/PR and inspect the diagnostic artifact before deciding any source-recovery behavior.
- [x] Merge after checks; confirm the main diagnostic output and record the factual source availability, without changing the production HTML error state.

## Execution ledger

Baseline: clean linked worktree reused on feat/un-transport-evidence-20261111 at0958cd7. Existing33 UN tests pass. Prior full814/offline213 checks passed before this change.

Ruling: cloud-packaged helper scripts are not readable in this runtime; track the execution ledger and test logs in a local ignored workspace, with completion evidence summarized here. This changes bookkeeping only.

### Task3: Explicit production challenge classification after measured evidence

**Files:** Modify src/un_sources.py,src/un_watch.py,src/un_transport_probe.py and tests/test_un_watch.py. Curate GitHub JSON/diagnosis under docs/evidence/un-transport-20261111/.

**Interfaces:** access_challenge(headers)->str returns only challenge/captcha or empty; UNAccessChallengeError derives from UNTransportError. The watcher's read path rejects an explicit challenge before document parsing/archiving; diagnostic uses the same predicate.

- [x] RED: HTTP200/202 explicit challenge produces a transport challenge error and preserves XML/previous document/master; empty202 without header stays generic; healthy CloudFront responses remain valid.
- [x] Implement the narrow predicate/error and read-path guard.
- [x] GREEN focused tests, then the full suite/offline checks; record measured GitHub evidence and RSS unavailability.
- [x] Include this task in the one final whole-branch review and PR checks. Validate new main run classification after merge, preserving explicit HTML errors and the master.

Ruling: extend this same diagnosis change with source-error classification only after the GitHub header evidence confirmed the cause. It changes error specificity, not source acceptance or import decisions; an erroneous broad classification would create false outages, so explicit-header and healthy-CloudFront tests guard it.

Task1 safety addition: a controlled --out data/state.json reproduction corrupted a temporary sentinel under the original CLI. Added RED→GREEN protection for business paths, symlinks, unrelated existing JSON and non-JSON outputs; own reports remain refreshable. This is part of the original no-business-writes requirement.

## Completion evidence (2026-11-11 JST)

PR #64 merged as7f25b3ce5d9b6bb6eb790242745bc4a85b59d07a after all checks passed:828 Python tests and213 offline checks. Independent review findings (relative signed URL in exceptions, unknown RSS encoding, UTF16 DTD) were reproduced RED and fixed GREEN in one pass; no deferred findings.

Main diagnostic run42851555206 completed. Main monitoring run42851555208 preserved explicit source failures while its observation publication succeeded, producing d6deb0f01736263e2d841dc9f11d62f6b6a69e51. Its15 failures are UNAccessChallengeError, HTTP202, fetch_failed=1, schema_changed=0, with no raw challenge archive. XML SHA256 bf4987beac908415f9148bb8cab2eb3796724f0ca54129b418dd146e7d9caf9b retained; master bytes unchanged from parent (SHA256 acf78ff686bddaad01c1b258f73801af3683e0a9e9e7177fe141e0be773b136a).

XML remains available; GitHub HTML/RSS retrieval remains blocked by explicit access challenges. Diagnostic completion does not imply source recovery or approval/application of pending UN reviews.
