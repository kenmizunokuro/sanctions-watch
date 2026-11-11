# UN official press RSS auxiliary sensor

Extend the authorized UN monitor with https://press.un.org/en/rss.xml, verified in
PR65 on the same GitHub runner. This is a bounded global-news sensor, never a
replacement for committee indexes or XML. Preserve all primary source failures.

Parse RSS2 with one channel whose link is https://press.un.org/en. Require 1–200
items with unique nonempty GUIDs and official HTTPS article links, plain bounded
titles, and timezone-aware publication dates. Reject DTDs in every encoding,
malformed XML, future dates over ten minutes and invalid/duplicate fields. Match
only SC article URLs with sanctions/asset freeze/arms embargo/travel ban in the title.
This keyword filter can miss articles and never establishes a legal action.

Track all items for overlap. First poll records a baseline without new-item alerts.
Subsequent new/revised matching items become PENDING_REVIEW with first detection,
source hash, official URL, previous version and a chained event ID (A/B/A is new).
Compare semantic item fields, not the feed hash. Retain prior versions and pending
events; URL disappearance is not a delisting. A relevant title revised to unrelated
content remains reviewable. Never write master or apply a feed event to XML.

A successful poll older than 90 minutes or no overlap creates a persistent coverage
gap. Newest publication older than 72 hours is STALE, separately from transport or
schema errors. New publication freshness cannot prove complete coverage. Repeated
errors retain the last validated document/items and prior candidates.

Use existing UN atomic state/review/audit publication and writer lease. Add a separate
RSS summary, candidates, errors and gap to existing 11-column review CSV; Sheets
already preserves event-ID memos without new schema or GAS changes. Existing primary
status rows retain their meaning. Include independent sensor status/counts in CLI.
Any RSS failure/staleness/gap makes monitor exit nonzero, without masking other errors.
Baseline produces zero new notices; optional existing webhook only gets new pending
RSS candidates, distinctly labelled auxiliary/master-not-applied.

Add explicit hash-bound rss-review and rss-coverage-review commands with operator,
timestamp and reason. Coverage clearance needs a recent nonstale observation and
records the original gap; subsequent detected gaps remain independent. No implicit
approval of XML, master, primary HTML coverage or other pending events.

Implementation choices within the user's continuation authorization: existing Python,
same fixed endpoint/collector/redirect policy, 72h content and 90m polling thresholds.
No new scheduled job, external account, notification destination or challenge solving.
