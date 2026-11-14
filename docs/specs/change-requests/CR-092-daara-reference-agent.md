# CR-092: the DAARA reference agent, alert handling and the optional explanation (ZID-AG-WF-001 v1.0; ZID-AG-WF-002 section 15)

Status: **applied, default `shadow`, runs are manual.** Built on the engineering owner's instruction of 2026-11-14 to complete Agent Wireframe 01 and the open items of CR-091. The document is a decision-ready proposal, not an approval: decisions D-01 to D-09 and gates G0 to G4 are the owners'. Nothing here makes the agent generally available.

## Why

CR-091 built the governance plane (leases, approvals, budgets, quotas, the stop). Nothing yet ran under it. Wireframe 01 asks for the smallest real agent that proves the chain: identify the actor, evaluate a delegated mandate, authorize each tool use, and keep decision-grade evidence, with one write that cannot change access.

## What changed

| Where | Change |
|---|---|
| `machine/domain/daara.py` | Pure: the three-tool template, six versioned rules R-01..R-06, proposal planning and dedupe, run classification, the explanation checker, the setup hash and stage, the synthetic sandbox fixture. |
| `machine_0009` | `daara_instance`, `daara_run_result` (final, never edited), `review_proposal` (the finding is immutable, only a person's decision changes), `alert_state`. Row-level security on all four. |
| `machine/application/daara.py` | Setup, validate, dry run, request-publish, the governed run, the review decisions and the optional model rewrite. |
| `machine/api/daara.py` | 14 routes. The run takes an Idempotency-Key. |
| `mandate/ports.py` | `metadata_projection`: the one read-only view DAARA gets of mandates (id, state, end date, allowed actions, source-grant actions and status). No person, scope, value or purpose text. |
| `governance.py` | Alert state: a condition that holds is recorded once (grouped by key, counted), starts an SLA clock, can be acknowledged and routed, and is resolved when it stops holding. A first publish with AI on now needs the AI signers (Privacy, Finance) as well as Security and Authority. |
| Governance hook | In shadow mode a company with no baseline records nothing (see "Found while building"). The freeze check still runs for everyone. |
| Actions | `agent.daara.read/manage/run`, `agent.review.read/decide`, `agent.alert.manage`, and the three tool actions the template names. The two reads join the Owner's console grant. |
| Console | The setup wizard (five steps, each locked until the gate before it passed), the run page, the review queue and proposal page, and acknowledge / route on the overview. |

## What DAARA is, precisely

It reads mandate metadata and agent identity status (each read governed: lease, region, company baseline, the approved policy for its exact hash, quotas, freeze). It applies fixed rules. It writes one thing: a row in the review queue, once per (target, rule, version, finding, facts), counted when seen again, capped at 25 per run with the rest folded into a summary. It has no free-text input anywhere, so text planted in a record cannot reach a rule, a proposal or a model. A run with anything it could not establish is `NEEDS_REVIEW` and `incomplete`, never `COMPLETED`.

A proposal changes nothing on open or on acceptance. "Open governed access workflow" records a digest-bound hand-off for the access-change service to start from; that service asks for its own permissions and a human. An agent cannot decide a proposal.

## Optional explanation

Fixed text from the counts is the default and always the fallback. A model rewrite is used only when the setup asks for it, the policy has AI on (signed by Security, Authority, Privacy and Finance), the company approved the provider by name, an operator registered it, a spending cap exists and the reserve was granted. The model receives counts, rule codes and masked targets only. Its answer is checked: no number or identifier the findings do not hold, no claim that anything was granted, revoked, approved or run, no instruction-like words. Any failure (no provider, no budget, a timeout, an error, an unfaithful answer) uses the fixed text, releases the reservation and writes a fallback row. No provider is registered by default and none ships.

## Found while building

- **A hot-path cost.** The gateway hook wrote a hash-chained evidence row, behind a per-company lock, on every call, even for a company that never set up governance. The existing revocation-propagation test (tool calls in a tight loop) measured about 2.1 s against a 2.0 s budget because of it. In shadow mode a company with no baseline now records nothing. The evidence chain is now one chain per agent (and one for company-level rows), each starting from nothing and locked separately, so a company that has adopted governance pays one lock and one insert per governed call that no other agent waits for.
- A first publish with AI on had been classed as a plain publish, skipping Privacy and Finance. Fixed.
- **A multi-company collision.** The event outbox keys events on (type, id, version) across the platform, but versions are counted per company, and a tool id is chosen by a person. A second company registering a tool with the same id (every company enabling DAARA does) hit a duplicate-key error. Tool events now carry a company-scoped aggregate id; the audit record keeps the plain tool id.
- The plan entitlement was a constant. Every governed call now reads it through the commercial resolver (CR-091 updated). The evidence export (CSV, redacted by role, formula-safe, itself audited) was added under `agent.audit.export`.
- Reading a dry run, a quota or a freeze never touches a mandate: the tests read the mandates and the agent registry back byte for byte before and after.

## What it deliberately does not do

- A run can be started inline (201 and the result) or queued (`?mode=queued`: 202, state QUEUED). A queued run is counted against the agent's quota when it is accepted, and a worker re-checks the freeze, the agent, the sponsor and the approval when it claims it; a freeze cancels it instead.
- It does not obtain a gateway grant or a workload token per read: it runs inside the platform and calls the governance gates directly. The PDP and token broker steps of the document's sequence are not exercised.
- A daily schedule exists but cannot be switched on today: it needs the agent's approved policy to hold `schedules_enabled`, the company baseline to allow schedules AND the platform ceiling to allow them, and the shipped ceiling is off. One run per agent per UTC day, by key. No customer-supplied agent, no tool upload, no ZoikoVertex contract.
- It notifies through the alerts and tasks inbox only: one open task per agent ("An agent proposed an access review"), folded and counted by later runs. It sends no email or message of its own, and a failure to notify never changes a run.
- It does not decide KYC, KYB, qualification or biometric questions, recover accounts, edit policy or spend outside an approved budget.

## Owner decisions still needed

D-01..D-05 (adopt DAARA, the write limit, AI explain-only, region-pinned worker), D-06 and D-08 (the real release state and whether the first deployment uses this substrate or ZoikoVertex), D-09 (brand assets), and everything CR-091 lists. Gates G0 to G4 of document 01 section 14.

## Evidence

`backend/app/tests/unit/test_daara.py`, `backend/app/tests/component/test_daara_pg.py`, `backend/app/tests/component/test_agent_governance_pg.py`, `frontend/apps/web/src/features/admin/agents/governance/daara-logic.test.ts`, `frontend/tests/e2e/agent-governance-a11y.spec.ts` (written, needs the local stack to run), `docs/qa/agent-daara-matrix.md`.
