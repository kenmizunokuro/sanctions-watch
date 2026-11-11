# RSS review original alignment

Synthetic-data reproductions found that the prior review checked archive bytes/hash
but trusted separately cached candidate/freshness metadata. Seven initial cases failed:
fabricated current/previous titles, inconsistent event identity, missing previous-source
provenance and coverage metadata that disagreed with its correctly hashed original.
These are test reproductions, not evidence of production data corruption.

The fix reuses the strict offline RSS parser, records prior-original provenance for
new revisions, checks both versions against their exact original and fails without
writes on mismatch. An exact retained parent can recover provenance for a legacy
revision; unknown associations remain pending. IDs and detection timestamps remain stable.

Current saved production RSS was checked read-only in `current-original-check.json`:
10 parsed articles agree with cached records/latest publication. There were no live
pending RSS candidates. State, master and review CSV hashes were unchanged by the check.
No production candidate, XML or coverage decision was approved during this work.

Fourteen new synthetic cases and existing related tests passed (79 tests total), including
first relevant transition, title reversion, provable/unprovable legacy history, unavailable
original and no-write rejection. Full suite, independent review and CI outcomes are
recorded in the PR to avoid retriggering code-identical CI solely to record its outcome.

Independent review found one important association defect: a genuine older original
could substitute for the immediately preceding candidate revision. Reproduced as
A(candidate)→B(candidate)→C(non-candidate), then fixed with two failing-to-passing cases
for wrong predecessor/no-op revision and a passing exception for intervening unrelated
versions. The corrected gate binds relevant predecessor records to the retained parent.
Offline213/213 passed. Final full-regression evidence comes from the final PR CI.
