# MOF monitoring-stage repair

**Goal:** Report successful MOF retrieval independently from blocked publication, without changing master or approval policy.

**Root cause:** The 2026-10-30 candidate removes MOF attribution from six accepted legacy strings. Exact approval binding correctly rejects that candidate. `mof_failure` currently collapses any downstream failure into a retrieval error and loses successful fetch evidence.

**Constraints:** Keep both integrity and approval gates. Never relabel network/schema/runtime failures as review holds. Preserve applied count/hash/as-of. Bind retrieval evidence to the exact workflow run and attempt. Do not alter master, acceptance ledger, source baseline, changes, or screening exports.

## Work
- [x] Add failing tests for validated current-run receipts, stale attempts, blocked audits, actual retrieval errors, and dashboard freshness.
- [x] Have `mof_pending` write a validated retrieval receipt after complete diff generation; expose workflow outcomes to failure persistence.
- [x] Enrich failure evidence with independently checked fetched hash/raw and valid blocked-audit report; project explicit held status in Python and GAS.
- [x] Verify focused Python/Node tests, full offline suite, actual failed-run artifacts, and unchanged protected file hashes.
- [ ] Publish a PR, check CI, merge the repair, then verify the production MOF execution and resulting status.
