# Preserve queued source-monitor runs

## Observed failure

OFAC run42774569426 attempt1 retrieved its sources but stopped before publishing because the newly introduced MOF weak-alias gate found310 blocking active names. The approved358-row migration subsequently completed in run42775224751, with zero combined audit blockers and a committed APPLIED/DELIVERED journal.

Attempt2 of the OFAC run was pending behind crypto42776172952, then cancelled at2026-11-10T07:40:57Z when another shared-group run was submitted. JP42776984605 was also cancelled. All eight writers used the same `sanctions-watch-commit` concurrency group with `cancel-in-progress: false`, but omitted `queue`, whose default is `single`. Keeping the running job does not keep multiple pending jobs.

## Repair

Set `queue: max` in every declaration of this shared group, at both workflow and job scope. Keep the group name and `cancel-in-progress: false`. Publication remains serial, and up to100 waiting instances are retained rather than replacing the previous pending instance. Existing workflow triggers, data approval gates and atomic publication checks remain in force. Already-applied MOF migration runs remain idempotent through the existing receipt/delivery journals.

Official references, checked2026-11-10:

- https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency
- https://github.blog/changelog/2026-05-07-github-actions-concurrency-groups-now-allow-larger-queues/

## Validation

All workflow files parse as YAML. Recursive validation found exactly8 shared-group declarations; all use `queue: max` and `cancel-in-progress: false`. `git diff --check` passed. Independent review found no Critical/Important defects. Validate runtime recovery with a NEW post-merge OFAC push run: retrying an old workflow run retains its original concurrency definition.
