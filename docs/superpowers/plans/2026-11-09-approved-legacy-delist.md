# Publish the already approved six legacy delistings

The six-name user approval from 2026-11-08 exists only in local commit 588a4c9. Latest main still contains the six exact approved before rows. Bring the unchanged approval artifact into main and materialize only its six transitions there. No new retention or delisting decisions, no source snapshot promotion, and no relaxation of the general integrity gate.

- [x] Add regressions for exact six-row application, changed-row/approval rejection, ledger withdrawal, history preservation, failure atomicity and idempotence.
- [x] Stage and validate master, remaining 2,423 acceptances, inactive search-list rows, active-only screening, six published delisting events and removal of only those six legacy review cases. Publish all outputs atomically with an application receipt.
- [x] Make legacy-review generation accept the validated remaining ledger and retain historical fixtures for six-case regression tests.
- [x] Add a one-time, concurrency-protected main workflow that commits only this publisher's output allowlist after full tests; do not promote unreviewed source candidates or rewrite source status.
- [x] Run Python/GAS/offline checks and independent review.

Release procedure: verify PR CI, integrate main and verify the one-time application workflow's published generation. The source monitor's existing unresolved audit findings remain separate.

Review focus: partial prior application, changed selected rows, unrelated concurrent changes, invalid/expired approvals, mixed output generations, reapplication duplicating events, failure before push. The three OFAC names CARLOTA C, GIRALT and NEDAS have no retrieved user decision and remain outside this change.

Independent review found a queued monitor could run from its old triggering commit and restore the six completed review cases after application. Reproduced with a real bare origin (RED). Fixed by checking out current main after acquiring concurrency, binding generation receipts to the exact original master bytes, and withholding stale-base publications against fresh main. Exact bytes are read from Git without newline conversion. The stale-generation, date-preservation and concurrent-clear regressions passed GREEN. No deferred review findings.

Approval provenance: unchanged bytes from local commit 588a4c90545f277d25d995592e04c4523927a965, SHA256 223a3be4ed97ea2e75c0a059cf8f286a09ae22f76f7267eae851cd57dc093c95. Latest main's six before rows match exactly. Source publication/approval timestamps and actual application time remain distinct in the application receipt.

Final local verification: Python 750/750 on both the preapplication tree and a separate materialized postapplication tree; GAS 75/75; offline 213/213. Five real-data publisher tests cover exact scope, idempotence, preserved unrelated reviews/history, changed approval/row rejection and two failure paths including mid-replacement rollback. Three focused freshness/race tests also pass.

Production follow-up: PR #56 merged, but GitHub canceled the dedicated apply run 42547968327 before scheduling any jobs: a higher-priority pending request existed in the shared concurrency group. Failed-job retry is unavailable for that jobless canceled run. Keep the dedicated workflow manual-only and invoke the same fixed transition inside the already-serialized ordinary monitor before polling. The shell entrypoint requires a clean generation, publishes only the fixed output allowlist, and never rebases rejected publication.

The monitor skips a prior one-time journal only when its bytes are committed locally and present on current remote main. Independent review reproduced an interrupted/unpushed journal being mistaken for published; the real-bare-origin regression was extended RED→GREEN for both states, successful first publication, duplicate execution and a corrupt journal. Future legitimate source updates to the six names are not frozen by this one-time publication journal.

Monitor-entrypoint verification: full Python 751/751 after the review fix; the new real-origin publication/retry test also passed independently. Existing GAS/offline suites remain 75/75 and 213/213. The data transformation itself is unchanged from the separately verified pre/postapplication trees.
