# Official press RSS auxiliary sensor verification

PR65 established GitHub availability of the officially published endpoint. This change
uses it inside the existing scheduled UN monitor, independently of the blocked HTML.
`local-preview.json` is a real ordinary-collector fetch parsed and observed in an isolated
temporary directory: HTTP200, 10 items, 0 matched sanctions titles, 0 new baseline
candidates. It is not a production monitoring result or a full committee-coverage claim.

The implementation stores all ten items for overlap and only relevant SC news transitions
as reviewable events. A fresh transport response cannot clear primary HTML failures.
It never applies names or actions to master. Existing review CSV11/memo IDs are preserved.

Test-first evidence: six parser/observer tests initially failed on the absent module;
five integration tests initially failed on absent monitor/review support. Separate audit
counts and status-CLI tests also failed before implementation. Related65 Python tests
and3 GAS tests passed after changes; full regression/CI results are recorded in the PR.

Independent review found two operational defects, both corrected: recheck RSS publication
age at coverage-review time, and accept the `rss:` event prefix shown by the CSV. The
former was reproduced in a failing test, then passed; displayed IDs are now used by the
integration test and exposed by `un_review status`. No other Critical/Important issues
were reported. The final workflow execution must confirm actual main publication,
explicit primary errors, RSS baseline, original archives and unchanged master.
