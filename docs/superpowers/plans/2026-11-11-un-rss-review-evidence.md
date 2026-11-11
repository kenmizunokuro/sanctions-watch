# RSS review evidence implementation plan

> Implement inline with test-first verification and an independent final review.

**Goal:** Require candidate and coverage metadata to agree with saved official RSS bytes.
**Architecture:** Reuse archive hashing/parser and existing atomic review projections.
**Spec:** `docs/superpowers/specs/2026-11-11-un-rss-review-evidence-design.md`

## Global constraints

No network acquisition in review, no automatic approvals, no master writes, no changes
to IDs/first detection timestamps/CSV. Freshness remains72h; current poll age90m.

## Review focus

Forged current and previous records despite valid hashes; semantic association/ID
mismatch; legacy revisions with/without provable parent; unchanged state on rejection;
stale/forged coverage metadata; correct normal new/revision/reversion reviews.

- [x] Reproduce7 failures against the existing implementation with saved synthetic originals.
- [x] Add previous-source provenance, parsed-original candidate and gap validation.
- [ ] Add positive/legacy/reversion/broken-original cases and run targeted/offline/full checks.
- [ ] Independent review, test-first important fixes, PR and CI, merge within authorization.
- [ ] Verify main implementation and current saved original read-only; record results in PR.

Results belong in the PR so recording its own CI outcome does not retrigger identical CI.
Related79 tests and offline213/213 passed. A read-only check of the actual saved RSS
validated10 articles against cached records/publication and preserved all business hashes.
Full regression and independent review are in progress; final execution outcomes are in PR.
Independent review completed: one important predecessor-association finding corrected
with two RED→GREEN tests and a legitimate-intervening-version case. No other findings.
The final complete suite is verified by PR CI; a local run started before this fix is
intermediate evidence only and is not claimed as verification of the final code.
