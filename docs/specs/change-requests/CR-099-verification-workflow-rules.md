# CR-099: Verification workflows as data: static safety analysis, simulation and publish gates (ZID-GAP-001 v2.0 DTV-30)

Status: **rules, store, executor, API and console built (the second slice).** An approved workflow runs for an applicant only after a different person approves it; nothing is called certified. Who may author and approve workflows is still a product decision.

## Why

DTV-30 asks for a workflow studio: a customer or compliance team defines which checks run and what happens on each result. The risky part is not the picture editor; it is that a workflow can quietly approve someone it should not, loop forever, or drop an unexpected result. This change builds the part that makes a workflow safe to store, review and later run.

## What was built

`verification/domain/workflow.py` (pure), `config/workflows/standard-onboarding-SAMPLE.yaml`, `verification/infrastructure/workflow_registry.py`.

- **A workflow is data.** Steps of five kinds (CHECK, DECISION, REVIEW, NOTIFY, END) and a fixed vocabulary of conditions (`check` with `outcome_in`, `all_of`, `any_of`, at most three deep). An unknown key, a script, or an expression is refused. Nothing in a definition can run anything.
- **Static safety analysis (`analyze`), checked before publish:** it starts and every step is reachable; every branch target exists; there is no loop; there is an end; no more than 50 steps and 2,000 paths (too complex to review is refused, not guessed at); every DECISION has an `else`, so an unexpected result goes somewhere on purpose.
- **The system never declines anyone.** The only end outcomes are APPROVED, REFER_TO_PERSON and COMPLETED_NO_DECISION. There is no DECLINED.
- **APPROVED is guarded.** On every path that ends APPROVED, each required check ran and the path was taken only because that check came back VERIFIED (VERIFIED_LIMITED only if the workflow allows it). The `else` branch is never a route to approval, and `any_of` guarantees only what every alternative shares.
- **A regulated check never auto-approves.** A person (a REVIEW step) must stand between AML screening or source of funds and APPROVED, after the check ran.
- **A product line that is not available blocks publish** for a check that needs it (source of funds needs the `source_of_funds` line).
- **Simulation (`simulate`).** Replays a definition against supplied results, with no side effects. A check with no result is UNAVAILABLE, never assumed fine.
- **Publish gates (`may_publish`).** The author cannot approve their own version, and the approver must be named. A version has a content hash; an approved version never changes (a change is a new version).

## The rules (each has a test)

1. The brute-force property: for every assignment of results to the two required checks in the sample, the workflow ends APPROVED exactly when both are VERIFIED.
2. A malformed definition is refused whole. A workflow that approves without a guard, skips a required check, loops, has a dead end, or reaches an unreachable step is refused at publish.
3. Every result other than VERIFIED ends with a person, never an approval.

## Second slice: store, executor, API and console

- `verification_0016` adds `workflow_version` (frozen once approved, never deleted, approver differs from author) and `workflow_run`, both under row-level security.
- `workflow_run.py` is the executor: a run waits for one named check or one named role, accepts only the event it awaits, stays on the version it started on, and ends where the simulator says it would for every combination of results. The applicant cannot review their own run.
- `workflow_service.py` with `/v2/workflows` and `/v2/workflow-runs`: check, draft, simulate, approve (a different person, only when analysis is clean), retire, start a run, report a check, complete a review. Each writes an audit event. Governed erasure empties a run's results.
- Console: `/admin/workflows` (list), `/admin/workflows/new` (editor with Check and Save draft), `/admin/workflows/{id}/{version}` (analysis in words, simulate, approve, retire). The menu shows them to administrators only.

## What it does not do

- A check step does not call the check services itself: a caller reports the result.
- There is no runs list screen yet.
- It does not decide what a good workflow is for a given market: that is the compliance team's.

## Owner decisions still needed

1. Who may author a workflow and who approves it (the maker and the checker roles).
2. Whether customers may author workflows at all, or only Zoiko's compliance team, and for which markets.
3. The retention of workflow versions and of simulation runs.
4. The executor: how a running workflow is stored per applicant, how long it waits for a person, and how it is cancelled.

## Evidence

`backend/app/tests/unit/test_workflow.py`, `test_workflow_run.py`, `backend/app/tests/component/test_workflows_pg.py`, `frontend/apps/web/src/features/admin/workflows/workflows.test.tsx`.

## Sign-off (UNSIGNED)

| Role | Name | Date | Decision on the four questions above |
|---|---|---|---|
| Product owner | | | |
| Compliance officer | | | |
