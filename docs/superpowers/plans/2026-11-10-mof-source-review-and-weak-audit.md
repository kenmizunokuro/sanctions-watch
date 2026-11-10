# MOF source review and weak-alias audit implementation plan

> **For agentic workers:** Use superpowers:executing-plans for the approved application. Review the exact proposal first.

**Goal:** Resolve the 79 MOF source-change candidates without admitting weak-only names into ordinary automatic screening or confusing name revisions with Party delistings.

**Architecture:** Keep the existing source poll, independent integrity reader, publication veto and explicit retention ledger. Extend the read-only auditor's quality checks. Preserve the entire proposed data migration as before/after rows and source evidence; publish business data only after the user approves its concrete impact.

**Tech Stack:** Python 3.12, CSV/JSON/gzip, existing GitHub Actions and atomic persistence.

**Spec:** User project instructions: primary-source traceability, Weak Alias must not be independently screened, true first-detection timestamps, reviewed/approved/applied separation. Concrete proposed changes: `docs/evidence/mof-source-review-20261110/README.md`.

## Global constraints

- Source79 first detection remains 2026-11-05 05:00:03 JST; newly discovered quality findings use their separate November10 observation.
- Preserve existing first_seen_ms, unrelated rows, raw originals and all earlier audit/application records.
- Preserve 28 current strong OFAC-supported names:26 literal,2 exact existing-approved current strong typed forms.
- Inactive reference-only names remain in the evidence/master history and are excluded from the screening export.
- Keep the production MOF publication gate enabled; no approval is fabricated by this proposal.

## Review focus

- A valid OFAC-only strong registration must not be blocked by MOF weak evidence.
- MOF source claims in category prefixes must not bypass the quality veto.
- OFAC typed evidence must come from the real hash-pinned collector schema.
- Expired, historical, weak, unapproved or changed-row approvals must never release the veto.
- The six earlier approved delist rows and all unrelated data must remain unchanged.

### Task1: Source and impact review

- [x] Re-fetch the official November5 CSV and verify SHA256 against the rejected production candidate.
- [x] Reconcile 76 field revisions and3 Party removals using source IDs and official Japan/UN notices.
- [x] Distinguish31 remaining Party-delisted names from7 same-Party name revisions.
- [x] Review341 weak-only MOF names; propose313 reference-only names and retain28 supported strong names.

### Task2: Quality veto and regressions

**Files:** `src/integrity_audit.py`, `src/integrity_decisions.py`, `tests/test_integrity_audit.py`, `tests/test_mof_weak_strong_retention.py`.

- [x] Witness failing regressions before the quality-veto repair and review fixes.
- [x] Preserve current, exact, approved strong OFAC typed forms without a general MOF waiver.
- [x] Verify real collector integration, expiration, weak/history/unapproved evidence and changed-master rejection.
- [x] Obtain independent review and resolve all Critical/Important findings.
- [x] Final verification:805/805 Python,213/213 offline,0 blocking findings in the combined candidate audit.

### Task3: Concrete approval package

- [x] Preserve79 source candidates and their first-detection times.
- [x] Save358 proposed changed rows and341 quality decisions, plus4 proposed withdrawals of weak-only retention approvals.
- [x] Verify the proposed358 rows reconstruct the independently audited candidate byte-for-byte.
- [x] Published the reviewed package as Draft PR61; user subsequently approved the exact358-row impact.

### Task4: After the user's concrete approval

- [x] Revalidated latest main0c37293: all358 before rows and4 prior decisions match. Exact proposal SHA23381e958a4dd1f50259667b3a0c57017c34dd545db23f1d26460d32357daa00 is pinned to the user's approval.
- [ ] Apply the approved rows and source-state transition as one atomic publication with projections, raw source, source-record baseline, pending queue, change history and separate application receipt.
- [ ] Publish31 Party delist names,7 same-Party revisions,313 reference-only quality decisions and54 material information revisions with correct detection/application timestamps.
- [ ] Run the combined integrity gate and production monitor; verify public exports, latest differences and zero pending source candidates.

## Execution record

User authorization is recorded in `data/review/approved_mof_source_update_20261110.json` with instruction「よろしく」. No new permission step is required for this scope.

Implemented the one-time atomic application, two fresh official-source checks around the combined audit,412 correctly timed history/queue events, raw source/baseline advancement,79-item promotion history, explicit application receipt and frozen approved diff. The four original weak-form approvals are preserved as evidence while removed from the active ledger.

Application, source mismatch, changed-before-row, expiry and real filesystem rollback tests pass. A review identified post-push release/monitor recovery as Important. Failure injection against a real local bare Git origin reproduced the missing-stage boundary; the delivery stage now resumes separately from application, binds to the committed audited generation, rejects supersession, and writes a durable DELIVERED journal. Both release-failure and monitor-failure retries pass; already-completed delivery is idempotent. A source change/failure only on the second check leaves all files unchanged. All9 application/delivery tests pass.

Full regression, final real-source validation, remote CI and main publication remain in progress. The user's approved business rows are not yet published at this point in the execution record.
