# Official UN press transport comparison

Continue the authorized UN recovery investigation using the existing read-only probe.
The official press homepage at https://press.un.org/en publishes `/en/rss.xml`.
Unlike the consolidated-list RSS, its observed items have distinct article links.
Local HTTP200 does not establish GitHub availability, feed coverage, or legal changes.

## Approach

Extend the existing diagnostic with the official press homepage and its RSS, retaining
the original four targets. This compares both publishers in one GitHub execution with
the same collector. Direct production fallback is premature: the global RSS is bounded,
contains unrelated UN news and cannot establish complete committee coverage.
No proxy, changed client identity or access challenge handling is added.

## Contract

- Six fixed official targets; keep existing project User-Agent, URL validator, timeout,
  byte limit, response classifications and credential redaction.
- Apply the existing safe structural RSS statistics to both RSS targets. Do not output
  article text, target names, actions, raw documents or a claim of source recovery.
- Each target remains independent: a failed/challenged RSS cannot drop other results.
- Diagnostic output only; no master, state, source ledger, review or status changes.
- Preserve real local/GitHub evidence and describe remaining coverage limitations.

## Completion

Tests cover availability independent of failed legacy HTML, RSS challenges, malformed
RSS and HTTP errors. Existing redaction/output protection remains green. Inspect the
actual GitHub artifact before deciding whether a later auxiliary sensor is feasible.
