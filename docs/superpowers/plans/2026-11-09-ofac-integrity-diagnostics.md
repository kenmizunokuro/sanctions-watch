# OFAC audit failure diagnostics implementation plan

**Goal:** Preserve the exact blocking reasons for an OFAC attempt and display them in the existing review sheet without changing publication or retention decisions.

**Architecture:** The audit CLI emits the saved report path and compressed-byte SHA256 as Actions outputs. The failure recorder accepts only that hash-bound report and the candidate's exact master/state/index hashes. It publishes a compact diagnostic CSV alongside the failure receipt from the clean-main worktree. GAS displays it only when its timestamp matches the OFAC status generation.

**Tech stack:** Python unittest, gzip/JSON/CSV, GitHub Actions, GAS/Node.

**Scope:** No target-state changes, new approvals, expiry extensions or gate exceptions. Keep the existing status and pending CSV schemas. Missing/mismatched evidence must be explicitly unavailable, never a successful audit.

## Task 1: Bound diagnostics and review-sheet display

- [x] Write failing tests for exact report capture, nonblocking-finding exclusion, stale/hash/input/path rejection, and diagnostics unavailable after an audit crash.
- [x] Emit `audit_report` and `audit_report_sha256` from the audit CLI; wire both initial and post-rebase failures to the recorder.
- [x] Save report reference, blocking counts, bounded names/reasons in the attempt; publish `data/dashboard/ofac_integrity.csv` only through the existing failure allowlist.
- [x] Show a matching-generation summary in A3 of `14_OFAC差分候補`, including when there are no change candidates. Clear stale summaries on success or unavailable/mismatched diagnostics.
- [x] Verify the real shell gate outputs and local bare-git publication: no candidate master/state leak, diagnostics survive publication, a newer success hides old reasons.
- [x] Run Python and GAS suites, review the diff, publish a PR and verify CI.

## Review focus

- An audit crash can produce no report: preserve failure and explain unavailable details.
- A post-rebase audit can differ from the first audit: use its exact output rather than selecting a report by filename/mtime.
- A CDN may mix status, pending and diagnostic generations: reject mismatched diagnostic timestamps.
- Names are untrusted source text: write diagnostic text as literal sheet content.
- Empty candidate CSV does not mean the integrity failure is resolved: still display its matching diagnostic summary.

## Verification and review

Python 720/720, GAS 75/75, offline 213/213 and standalone MOF record/re-review checks passed. Independent review found stale A3 after lightweight-sync failure; regression reproduced RED, invalidation added, full suites passed GREEN. No deferred findings. Live audit artifact 13620449304 reproduces three blockers: CARLOTA C, GIRALT, NEDAS. These remain unresolved retention decisions; no master or approval edits.
