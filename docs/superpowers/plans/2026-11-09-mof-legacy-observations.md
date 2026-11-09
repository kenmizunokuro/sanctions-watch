# MOF legacy review observation repair

Goal: Extend the existing first-detection requirement to the legacy review table. Keep its existing schema and audit gate.

The generator will write a run/attempt-bound receipt after writing the review CSV. Failure publication will validate that receipt, merge observation dates against fresh main, and record the published CSV hash separately. Reconciliation will verify this hash while continuing to compare the current audit's exact candidate row hashes and changed fields. Empty validated generations clear the table; failed generations preserve prior evidence and report withholding.

Constraints: No master, acceptance, retention, source state or screening edits. No new dependencies or GAS changes. Different candidate content starts a new observation; a cleared candidate's recurrence cannot inherit an old date.

- [x] Reproduce date overwrite, stale publication, empty clearing, and mixed-generation reconciliation failures with tests.
- [x] Add receipt validation and first-observation merge; preserve fresh-main behavior on push retries.
- [x] Verify run/attempt, CSV/hash/schema, future-date and changed-content rejection.
- [x] Run Python, GAS and offline suites; review.

Release procedure: publish the reviewed branch as a PR, verify its CI and integrate under the existing authorization. Verify the next production receipt separately from the still-blocked business-data audit.

Review focus: acquisition failure with an old tracked CSV; empty generation after earlier candidates; concurrent clear/republication; exact candidate identity; historical observations with a current audit.

Independent review identified valid overlapping-run observations rejected by reconciliation and non-object receipt JSON aborting persistence. Both reproduced RED, corrected and covered. A foreign-repository observation was reclassified as important because it must be withheld before publication, also reproduced RED and corrected. No deferred findings.

The legacy full-row case-ID format remains reconcilable. Migration inherits dates only when the exact prior row hash is unchanged and every changed business field is visible in the old CSV. This avoids inferring hidden old values. No historical dates have been backfilled in this change.

Final verification: Python 744/744, GAS 75/75, offline 213/213; standalone MOF record diff 18/18 and re-review 7/7. Focused legacy generation/reconciliation 20/20. All review regressions were observed failing before their fixes.
