# RSS review evidence alignment

Continue the authorized source-evidence hardening. Existing RSS review validates the
archive SHA256 but accepts candidate fields and freshness cached separately in state.
Synthetic corruption reproduces acceptance of nonexistent current/previous titles,
an inconsistent event ID, and fabricated freshness/current items during gap clearance.
No production corruption has been established.

Use the existing offline original/strict RSS parser to revalidate review input, without
network fetches or any automatic approval. Require current record membership in its
original, the existing event identity formula, and valid NEW/REVISION association.
New revision events retain `previous_source_hash`, pointing to the prior observed feed
or retained parent event original. Previous record must be in that exact original and
share the same article URL. A legacy revision can recover missing previous hash only
from a retained parent with exactly the same previous record; otherwise remain pending.
Preserve event IDs, detection times and existing history; do not rewrite old observations.
When the previous or parent version is relevant, the previous record must equal the
retained parent, so an older genuine version cannot replace the immediately preceding
one. Only intervening non-candidate revisions may differ from a non-candidate parent.
Identical previous/current records are not a revision.

Gap clearance revalidates current original records against stored items/source hash,
derives newest publication from raw bytes, and reapplies the 72h/90m criteria at review
time. Missing/mismatched/corrupt originals or metadata fail before any state/CSV writes.

Existing review CSV, notifications, automatic collection, XML approval and master logic
remain unchanged. No live candidate or gap is approved as part of this implementation.
Use synthetic fixtures and read-only checks of the current saved original for validation.
