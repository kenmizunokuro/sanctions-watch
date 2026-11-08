# MOF first detection preservation

**Goal:** Keep the earliest verifiable observation of each continuously open MOF source-diff candidate and its run URL. The latest fetch receipt must retain the actual latest check time.

**Architecture:** Preserve observation metadata during candidate generation and again against freshly fetched main during failure-only publication. Match the stable candidate ID and all business fields, excluding the changing raw archive filename. Invalid or future evidence must not be silently adopted. Preserve the rejected candidate receipt hash and separately record the published CSV hash if publication merges older metadata.

**Scope:** Existing Python/CSV/Git workflow; no master, approval, delisting, GAS schema, or notification changes. The separate legacy reconciliation CSV describes the current attempted master mutation and remains bound to that attempt.

**Approach:** Reuse the OFAC continuity rule at the MOF boundary. A permanent global earliest-date map would incorrectly reuse dates after a cleared candidate recurs; copying latest timestamps causes the confirmed defect. Only continuously present identical candidates inherit evidence.

## Task 1: Preserve first observations

- [x] Reproduce repeated-run timestamp/URL overwrite with a controlled clock and real MOF snapshots.
- [x] Add strict candidate reading, metadata retention, future/collision/schema checks. Preserve current raw filename and first observation URL; new/cleared events get new times.
- [x] Merge again in the clean-main publication retry loop; preserve monitor failure reporting if prior CSV is invalid. Record the published table hash separately from the fetched-candidate receipt.
- [x] Test actual bare-git publication, concurrent first publication, malformed prior evidence, and unchanged master/state.
- [x] Replay first-parent history to restore only verifiable continuously open candidate timestamps and save hashes/counts/evidence commits. Do not infer dates from official publication dates.
- [x] Run Python, GAS and offline regressions and review. Commit and GitHub validation follow below.

## Review focus

- Candidate IDs alone cannot authorize inheritance if names, hashes, before/after or source details conflict.
- Fetch raw filenames can change while their content hashes stay identical.
- A pending CSV cleared before a recurrence must break continuity.
- A push retry must re-read the latest main; old candidate receipt hashes must not pretend to describe merged publication bytes.
- Malformed prior evidence must preserve monitoring failure visibility without overwriting the table.

## Verification and review

- Baseline: Python 723/723. Confirmed timestamp overwrite RED; new observation tests GREEN.
- Independent review found two Important issues: candidate validation outside the publication guard could suppress failure persistence; a concurrent clear could resurrect inherited timestamps. Both were reproduced RED and fixed GREEN. Publication now starts from the exact current fetched receipt, then inherits only from fresh main. Candidate receipt run/attempt/count/hash must match before publication.
- Final Python 733/733; GAS 75/75; offline 213/213; standalone MOF record-diff and re-review checks passed. No deferred review findings.
- Replayed 18 first-parent candidate snapshots. Restored all 79 currently open candidates to 2026-11-04T20:00:03Z (2026-11-05 05:00:03 JST), with their first stored run URL. Source hashes, candidate IDs, business fields, current archive filename, master, state, and approval ledger are unchanged. Proof: `docs/evidence/mof-first-detection-restoration-20261109.json`.
