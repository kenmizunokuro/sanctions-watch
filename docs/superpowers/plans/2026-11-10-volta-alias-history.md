# Volta alias history implementation plan

> **For agentic workers:** Use superpowers:executing-plans, task by task.

**Goal:** Apply the user's 2026-11-10 approval to retain VOLTA SHIPPING SERVICES FZCO as a historical screening alias and let the ordinary OFAC monitor publish VOLTA CONTAINER LINE FZCO.
**Architecture:** An exact one-record approval binds the archived official alias, the Nov9 notice and current FixedRef58138 identity. A one-time atomic annotation/ledger publication runs in the existing serialized monitor, followed by the normal independently audited source update.
**Tech Stack:** Python3.12, CSV/JSON/gzip, existing atomic persistence, GitHub Actions.
**Spec:** User approval in this conversation: old alias retained as history; new alias added; publish differences and preserve real first detection.

## Global constraints
- Only the one approved old alias gets a new retention decision; no general audit exemption.
- Preserve old row first_seen_ms and last_updated_ms; no automatic delisting.
- Keep current names distinct from historical aliases and retain official URLs/hashes.
- New alias uses normal monitoring, pending first-detection preservation and audit gate.
- Follow existing approval expiry: 2026-11-25T08:21:15Z, after which re-review is required.

## Review focus
- Missing or changed current Party/name/alias/registration evidence must block.
- Tampered notice, original, approval or missing decision link must block.
- Partial publication must roll back all outputs; repeat runs must not duplicate events.
- Unpublished local receipt must not suppress publication.
- A different old name or unrelated blocker must keep its veto.

### Task1: Exact alias retention and safe publication
**Files:** src/ofac_alias_history.py; src/master.py; src/integrity_decisions.py; tests/test_ofac_alias_history.py; data/review/approved_volta_alias_history_20261110.json; docs/evidence/volta-alias-history/*; .github/scripts/apply-approved-volta-alias-history.sh; .github/actions/run-watch/action.yml; .github/workflows/watch-ofac.yml; .github/workflows/verify-mof-recovery.yml.
**Interface:** approval(root), verify_decision(root,report,decision), apply(root,now=None,run_url=''). Existing D.apply must invoke the verifier for the new approval marker.
- [ ] Build hash-pinned approval evidence from the saved historical source, official Nov9 notice and freshly fetched current source; record the user's approval verbatim.
- [ ] Write tests for exact one-row annotation/export/history, durable/idempotent publication, rollback and all five review focus cases; witness RED.
- [ ] Implement strict evidence verification, annotation preservation, atomic application and the existing durable-journal shell pattern; witness GREEN.
- [ ] Run the full Python/offline/GAS suites and real candidate audit, then obtain a separate whole-branch review.
- [ ] Publish PR, verify CI, merge under the user's authorization, and confirm actual one-time publication plus successful live OFAC monitoring and new alias first-detection history.

Progress: baseline main785 Python/87 GAS/213 offline passed in the preceding turn. Implementation uses an isolated branch from origin/main; baseline code unchanged since those checks. Fresh research fetch observes only1new alias. Approval scope and execution have already been authorized; no further permission step is needed.

Implementation record:9 focused tests passed, baseline full regression794/794 passed, GAS87/87 and offline213/213 passed. Real fresh candidate audit returned REVIEW with zero blocking findings. Independent whole-branch review identified one Important issue: a current-again old alias could lose its EXTRA_REGISTRATION finding and skip evidence checks. Two lifecycle regressions witnessed RED; independent marked-row validation now checks exact row/decision/expiry/current evidence even without a matching finding, and all11focused tests pass. Final full regression and updated real-data audit are running. No review finding was deferred; no scope/authority change was made.

Final verification: lifecycle2tests RED→GREEN; related existing retention gates and new tests19/19 pass. Full Python796/796, GAS87/87 and offline213/213 pass against the actual post-application dataset. The compatibility failure with minimal unmarked master fixtures was fixed by inspecting the marker before imposing the new approval schema. Updated real-data audit: REVIEW, publication_allowed=true,9922findings retained,0blocking. Exact impact is1new alias+1old-alias annotation with no deletions/other master row changes. Old first_seen/last_updated preserved; new alias retains2026-11-10 05:05:33JST first detection. Independent review's only Important finding is fixed; none deferred. Repository publication and live-run verification follow this commit.
