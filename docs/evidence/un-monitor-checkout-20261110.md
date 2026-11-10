# Start UN observation writers from current main

## Evidence

UN run42809833731 retry2 (monitor job129903438854) used the historical triggering commitf0cae64 while the current main had advanced to429e57a. The official HTML sources again returned HTTP202 with empty bodies, which the existing transport checks correctly rejected. The observation commit then failed to push with `HEAD -> main (fetch first)` because the writer checkout used the event SHA. The non-fast-forward rejection preserved current public data; evidence remained in the run artifact.

## Repair and validation

The monitor job now checks out `ref: main` after acquiring the job-level `sanctions-watch-commit` writer lease. The separate verification job still tests the event/PR SHA with credentials disabled. The shared queue, data-validation behavior and non-fast-forward publication veto remain unchanged. Computed source status is never rebased onto unseen observations.

All33 UN parser/monitor/review tests pass. YAML validation confirms fresh-main writer checkout, PR isolation, queue:max with cancel-in-progress:false, and direct guarded push without pull/rebase. Independent review found no Critical/Important defects. Operational validation must use a NEW post-merge workflow run; retrying an old workflow run retains its old checkout definition.

HTTP202 remains a retrieval error rather than unchanged or success. The official XML snapshot and master remain preserved when the HTML indexes fail. This change does not claim recovery of the official HTML transport.

## Prior approved migration confirmed

PR61 applied358 approved MOF rows and published412 change events, with79 import candidates cleared and9 re-review cases generated. Source/quality detection times remain distinct. PR62 repaired all8 shared queue declarations. New OFAC run42778716518 completed successfully at2026-11-10T08:28:04Z after two combined audits with zero blockers. Subsequent OFAC runs, including42825491994, also succeeded. Snapshot429e57a still has zero MOF import candidates and exactly412 tagged approved events.
