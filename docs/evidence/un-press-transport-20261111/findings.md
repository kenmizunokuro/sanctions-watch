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

## GitHub observation

Read-only PR workflow [42930184150](https://github.com/kenmizunokuro/sanctions-watch/actions/runs/42930184150)
completed. Its actual ZIP artifact 13815563325 was downloaded, SHA256 verified as
`442249359e35988eb726c1be696bc25e383466e8bc2dc720224689f856ffb831`,
and its JSON retained as `github-probe.json` (checked 2026-11-11T00:17:58Z).

| Target | GitHub HTTP | Observation |
| --- | --- | --- |
| Consolidated XML | 200 | Transport success; no legal validation implied |
| Consolidated update log | 202 | Explicit access challenge |
| 1267 committee index | 202 | Explicit access challenge |
| Consolidated-list RSS | 202 | Explicit access challenge |
| Official press homepage | 200 | Transport success; same body hash as local |
| Official press RSS | 200 | Well-formed RSS, 10 distinct GUIDs; same body hash as local |

The press publisher is demonstrably available to this GitHub runner, unlike the
existing HTML publisher. This establishes feasibility for a separate auxiliary
announcement sensor, not complete recovery. The feed also advertises a long cache
lifetime; HTTP200 and structural validity alone do not prove prompt update delivery.
A later sensor must distinguish baseline/revisions/new items, bound stale observations,
detect loss of overlap and preserve the existing HTML/committee errors. It must not
infer names, sanctions actions or master application from news titles.

## Verification

Three new tests first failed because the press endpoints were absent, then passed after
the extension. Related transport/source/watch/review tests: 50 successful. Offline:
213/213 successful. Independent review found no Critical/Important issues. Full local
regression: 831/831 tests successful (398.674 seconds). The GitHub `watch-un` verification
also succeeded for executable commit `96577b66b219090bd5ed98d52bccc88f99df3946`.
