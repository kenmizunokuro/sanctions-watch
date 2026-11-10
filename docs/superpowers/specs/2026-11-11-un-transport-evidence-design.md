# UN transport evidence design

## Intent

Diagnose why the official UN HTML documents return empty HTTP202 in GitHub while ordinary requests with the same project-identifying collector succeed locally. Determine whether the officially published RSS endpoint is available to the automation environment. This is transport evidence, not a sanctions-list importer.

## Existing evidence

The official homepage links to https://main.un.org/securitycouncil/en/feed/1.0/updates_unsc_consolidated_list . A project-UA fetch returned HTTP200, application/rss+xml,4794 bytes, SHA2562e280019fbf593ce744513305659f6444abcc90cdc0c6e851dfebb03638cb8ec. The RSS has10 items but only1 GUID and3 publication labels; all items point to the same update-history page. It cannot establish individual listings or delistings. Local project-UA update-history fetch returned HTTP200/278403 bytes; GitHub's existing production run returned empty202 for that document and all14 committee indexes.

## Approaches

1. Add diagnostics directly to production observation writes: captures the failing path but cannot compare the RSS before adopting it and expands financial-state changes.
2. Use a separate read-only probe, sharing the exact collector and official URL policy: observes success and failure in both environments without changing financial state. Chosen as an implementation decision within the user's authorized continuation.

Adopting RSS as a legal-list replacement is excluded: its current payload does not contain target IDs or action details. No user-agent impersonation, challenge solving, proxies or access-control workaround is introduced.

## Contract

- Exactly4 configured official targets: consolidated XML, update-history HTML, committee1267 index, and the RSS URL linked by the official homepage.
- Use existing project-identifying fetch and UN redirect validator; conditional requests disabled. 15-second per-request timeout, existing bounded transport retries, existing32MiB cap. Do not retry HTTP202 challenges.
- Report timestamp, original/final sanitized URL, HTTP status, byte length, SHA256 of received bytes, ETag/Last-Modified and a small public response-header allowlist. Never serialize cookies, authorization, raw body, or temporary signature queries.
- Explicit challenge header means ACCESS_CHALLENGE. Empty202 without that evidence means EMPTY_202, not an inferred WAF diagnosis.200 means HTTP200_UNVALIDATED, never validated sanctions content. HTTP errors/timeouts remain explicit observations; continue the other targets.
- RSS structural statistics only: well-formed RSS, item/GUID/publication-label counts. Reject DTD/entity-bearing XML; do not generate names, legal changes, approval decisions or detection timestamps.
- Pure collection API plus CLI JSON output under dist. No master, snapshot, state, reviews, heartbeat or public-release writes.
- A read-only PR/main/manual workflow publishes diagnostic JSON as an artifact. Job success means the diagnostic completed, not that sources are healthy. It uses its own CI concurrency group, not the shared financial writer lease.

## Completion

Offline tests prove classification, safe redaction, continued observations after errors, unsafe RSS rejection and CLI output without business writes. Full regression remains green. A GitHub artifact provides the actual same-environment result. Subsequent recovery work must follow that result; HTTP202 is not reclassified as unchanged.
