# CR-091: Delegated Authority & Access Readiness agent (DAARA), backend

Status: open (built and tested, default off; the console, the AI explanation, schedules and the Q2 limits are not built).

## Why
ZID-AG-WF-001 (v1.0, 12 November 2026) proposes one first-party reference agent: it reads minimal mandate and agent metadata, applies deterministic
rules R-01 to R-06 and appends a review proposal for a person. It never changes a right. The document's paths are illustrative ("must be reconciled
against actual Zoiko iD routing"), so this CR records the mapping and what is built.

## What is built (module `access_readiness`, new)
- Pure rule engine (`domain/rules.py`): R-01 expired, R-02 expiring (14 day window, set per setup), R-03 sponsor not active or unavailable, R-04 scope
  not a subset of the approved grant, R-05 evidence gap, R-06 unknown state. Versioned (`daara-rules/1`), takes a trusted clock reading, replays
  identically, and any input it cannot trust is UNKNOWN and blocked, never a pass.
- Setup (draft, dry run on synthetic fixtures, publish request, approval by a different person bound to the exact scope digest, pause, resume),
  a run state machine (QUEUED, PREFLIGHT, RUNNING, DENIED, COMPLETED, NEEDS_REVIEW, FAILED, CANCELED) with a fail-closed preflight, one idempotent
  write (a review proposal), review (dismiss, assign) and a signed handoff record that executes nothing.
- Seven tenant-scoped tables under row-level security in the `agent` schema, stream `access_readiness`: setup, approval, run, policy decision
  (append-only), evidence envelope (append-only, hash chained), review proposal, handoff (append-only, `executes` is always false).
- Read-only additions to other modules' `ports.py`: `machine.agent_identity_status` (status, sponsor, blueprint, verified attestation),
  `mandate.mandate_metadata_page`, `authority.grant_review_facts`. No write was added to any of them.
- Default OFF: `ZID_ACCESS_READINESS_ENABLED=false`. While off every route answers 404 (before authentication) and no run executes. Regions are the
  explicit allowlist `ZID_ACCESS_READINESS_REGIONS` (empty means none; a cell outside it fails closed).

## Path mapping (the document's illustration to this repository)
The repository has no `/v1/tenants/{t}/...` style: the tenant comes from the authenticated principal and is never in a path or a body. The agent
instance is an existing machine agent (`/v1/machine/agents`), so there is no second registration.

| Document | Here | Action (catalogue) |
|---|---|---|
| POST .../agent-instances | POST /v1/access-readiness/agents (setup for a registered agent; tenant, region server-set) | access_readiness.manage (R2) |
| PATCH .../agent-instances/{id} | PATCH /v1/access-readiness/agents/{agent_id} (optional `expected_revision`, the ETag role) | access_readiness.manage |
| POST .../{id}/dry-runs | POST /v1/access-readiness/agents/{agent_id}/dry-runs | access_readiness.manage |
| POST .../{id}/publish-requests | POST .../publish-requests, and `.../publish-requests:approve` by a different person | manage; access_readiness.approve (R3) |
| POST .../{id}/runs (Idempotency-Key, 202) | POST /v1/access-readiness/agents/{agent_id}/runs | access_readiness.manage |
| GET .../{id}/runs/{run_id} | GET .../runs/{run_id}, plus `/decisions` and `/evidence` (chain verified on read) | access_readiness.read (R1) |
| POST .../{id}/pause | POST /v1/access-readiness/agents/{agent_id}:pause (and `:resume`) | access_readiness.manage |
| GET .../agent-review/{proposalId} | GET /v1/access-readiness/review-proposals/{id}; `:dismiss`, `:assign`, `:handoff` | access_readiness.review (R2) |

Events: `agent.run.policy_denied`, `agent.run.completed`, `agent.review.proposal_created` (proposed-events.yaml). Role matrix routes are mapped in
`route-map.yaml` to the same matrix actions the machine routes use.

## Decisions taken while building
- A refused run is a recorded DENIED run (202 with `state: DENIED` and the reason codes), as the document says publication and run states are
  independent. Whether to answer such a request 403 instead is a console decision.
- A proposal key is tenant, target, rule, rule version and finding code. A dismissed finding therefore stays dismissed until the rule version
  changes. Owner to confirm.
- A handoff is signed with the `set` key ring (JWS, typ `zid-access-review-handoff+jwt`); without a key no handoff is recorded. Owner to confirm which
  key purpose should sign it.
- Execution is inline after the request is queued; `execute_queued` is the worker's pass and each queued run is preflighted again.

## Not built
- The four console wireframes (WF-AG-01 to WF-AG-04) and AT-22 (accessibility).
- The optional AI explanation and AT-15, AT-16, AT-17 (no model is called anywhere).
- Schedules, quotas, spend and approval thresholds (D-07, Q2). Only manual runs exist; `mode: scheduled` is refused.
- Short-lived scoped credential from a token broker (section 09 step 3): the run uses the machine module's verified attestation as its gate and holds no
  credential of its own.
- Open decisions that block a production release: D-06 (real release state, API inventory and role schema), D-08 (existing worker substrate or the
  ZoikoVertex runtime), D-09 (official brand assets). Release gates G0 to G4 are not evidence of anything here.
