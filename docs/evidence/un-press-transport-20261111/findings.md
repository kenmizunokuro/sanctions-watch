# Separate UN press publisher: feasibility evidence

The official homepage https://press.un.org/en links to `/en/rss.xml`. This is a
different publication feed from the consolidated-list RSS already measured.
Discovery used ordinary GET and the project-identifying collector, without client
impersonation, challenge handling or a proxy. The proposed change only extends the
read-only diagnostic; no fallback source has been adopted in production.

## Local observation

`local-probe.json`, checked 2026-11-11T00:15:18Z, reports all six configured targets
with HTTP200. The press RSS has 10 items, 10 distinct GUIDs and 10 publication labels;
the consolidated-list RSS has 10 items but 1 GUID and 3 publication labels. Neither
result is a claim that the content is current or that the automation host can fetch it.

The separate security-council press-release listing was also inspected during discovery
and returned an HTTP200 client-challenge document. It is excluded from the configured
probe targets. HTTP200 alone cannot establish that an official document was delivered.

## Scope limits

The global press RSS includes unrelated UN news and has only a bounded window.
An available RSS might be a future auxiliary signal; it cannot replace all committee
indexes, prove historical completeness, authorize master changes or clear their errors.
GitHub feasibility must be established by the actual workflow artifact.

## Verification

Three new tests first failed because the press endpoints were absent, then passed after
the extension. Related transport/source/watch/review tests: 50 successful. Offline:
213/213 successful. Full regression and GitHub observations are recorded after completion.
