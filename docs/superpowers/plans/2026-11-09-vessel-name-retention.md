# Approved vessel name history implementation plan

Goal: retain CARLOTA C, GIRALT and NEDAS as active historical screening strings, linked to the same vessels through exact OFAC FixedRef and IMO numbers.
Spec: user approval on 2026-11-09 (良いよ), project primary-source evidence and audit requirements.

Global constraints: only three approved rows change; first_seen_ms/last_updated_ms are preserved; old names remain active/high; new source candidates are never silently promoted. Historical evidence is visibly distinct from current names. Approval expiry is a re-review veto, never automatic deletion. No company data.

1. Bind the official 2026-11-05 notice, pinned historical CSV originals and exact current SDN vessel rows. Add current Party/name/type/IMO checks to the three retention approvals; missing/tampered/expired/stale evidence remains blocking. Preserve the independent history annotation during source label refresh.
2. Publish the exact three annotations, retention ledger entries, active list/screening exports, visible change events, evidence CSV and application receipt atomically. Integrate one-time publication before normal polling in the shared serialized monitor; durable journal must be committed and on remote main. Preserve unrelated differences and timestamps. Test scope, rollback, idempotence and committed journal behavior.
3. Keep the six already-approved delistings auditable against the exact superseded Oct21 MOF snapshot. Only their exact inactive after rows and official witnesses may be classified historical; new source snapshots, upstream failures and unrelated findings retain vetoes.
4. Run unit/offline/GAS regression and fresh-source full audit, independent review, PR CI, merge and verify actual main publication and live monitoring. MOF's separate unapproved candidates remain pending.

Verification: Python unittest 773/773, offline 213/213 and GAS 75/75 pass. Independent review and scoped re-review have no remaining blocking findings. Fresh real OFAC candidate after the migration audits REVIEW with zero blocking findings, preserving all 9921 findings. Exact three-row scope, unchanged source timestamps, atomic rollback and real-origin durable-journal behavior are covered. The OFAC workflow checks out latest main after the shared mutex and polls after its workflow deployment, allowing live recovery verification. The 22 new tests are part of PR CI and normal monitor self-tests.
