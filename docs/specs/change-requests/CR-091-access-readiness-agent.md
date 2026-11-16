# CR-091: Delegated Authority & Access Readiness agent (DAARA)

Status: open (built and tested, default off; schedules, the Q2 limits, the owner decisions D-06, D-08, D-09 and a manual accessibility pass remain).

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
  `access_readiness_0002` adds the setup's `ai_explanation` flag (default false).
- Read-only additions to other modules' `ports.py`: `machine.agent_identity_status` (status, sponsor, blueprint, verified attestation),
  `mandate.mandate_metadata_page`, `authority.grant_review_facts`. No write to another module's data was added; the machine lease below only records
  its own issuance and revocation evidence.
- Default OFF: `ZID_ACCESS_READINESS_ENABLED=false`. While off every route answers 404 (before authentication) and no run executes. Regions are the
  explicit allowlist `ZID_ACCESS_READINESS_REGIONS` (empty means none; a cell outside it fails closed).
- The four console screens (WF-AG-01 to WF-AG-04) in `frontend/apps/web/src/features/admin/agents/readiness/`, with component, keyboard and label tests.

### Short-lived scoped credential (section 09 steps 3 and 9; AT-07, AT-19)
- After the preflight passes, the run asks `machine.ports.issue_workload_lease` for a credential bound to the audience
  `zid:access-readiness:metadata-adapter`, the purpose `access_posture_review` and the scope of the three allowlisted tools, for at most 5 minutes
  (the workload token's own lifetime). The lease comes from the same issuance decision as `POST /v1/machine/workload-token`
  (`machine_store.credential_check`: agent ACTIVE, binding ACTIVE and in date, the approved artifact, no revoked blueprint, no quarantined
  environment, the verified attestation level against the binding and the risk-2 floor) and is recorded as the same
  `machine.workload.credential_issued` evidence. An unattested, under-attested, quarantined or revoked worker gets none and the run is DENIED with
  `CREDENTIAL_DENIED` and the machine code.
- `lease_problems` re-checks the lease before every read and before the write (expiry, audience, purpose, scope, a revoked or rebound binding, a
  changed security revision, a quarantine). A revoked or expired lease stops the run at that step with `CREDENTIAL_REVOKED` or `CREDENTIAL_EXPIRED`
  and nothing is written; no broader credential is ever asked for.
- Every way out of a running run revokes the lease (`machine.workload.lease_revoked`, audit-only) and the run's evidence chain holds
  `credential_issued` and `credential_revoked` envelopes.
- Gap (needs a product decision, D-08): the run executes in-process, so the lease is not a signed bearer token and there is no separate token broker or
  SVID for the worker. A lease holds no secret, so there is nothing to store or log. A broker-issued, sender-constrained token for an external
  worker belongs to the worker substrate decision.

### Sponsor notification (section 09 step 9)
- At every terminal state a run reaches by executing (DENIED, COMPLETED, NEEDS_REVIEW, FAILED, CANCELED; queued runs canceled by a pause are not
  notified one by one, the pause is itself a person's recorded act) the run raises one alert through `notification.ports.raise_alert` (kind `access_readiness.run.finished`, MEDIUM unless
  COMPLETED, a task when NEEDS_REVIEW) with the run id, its state and counts only: no person, mandate, grant or finding detail. A notification that
  fails never changes the run; its outcome is an evidence envelope (`sponsor_notified` or `sponsor_notification_failed`).
- Owner decision 2026-11-16: the alert stays in the workspace alert inbox (tenant-wide, no single recipient). Addressing the sponsor in person is not
  built.

### Export permitted evidence (WF-AG-03)
- `POST /v1/access-readiness/agents/{agent_id}/runs/{run_id}/evidence:export` with `{purpose}`: references and digests only (envelopes with the chain
  verified now, decisions, the proposals' rule, code, status and evidence references). Needs `access_readiness.read`; without
  `access_readiness.review` the export is redacted (resources reduced to their kind, targets and evidence references withheld) and says
  `redacted: true`. A missing purpose is refused with `EXPORT_PURPOSE_REQUIRED` and the refusal is recorded (`access_readiness.evidence.export_refused`);
  an export is recorded with its purpose's digest (`access_readiness.evidence.exported`). Route map: `f.request-sensitive-evidence-export` (S), as
  the other evidence exports. The run detail's button now asks for a purpose and saves the server's answer.

### Optional AI explanation (sections 06, 07, 09 step 8; AT-15, AT-16, AT-17)
- Two switches, both off by default: the cell's `ZID_ACCESS_READINESS_AI_EXPLAIN` and the setup's own `ai_explanation` flag, which is part of the
  scope digest the approver signs (turning it on means a new draft, dry run and approval). A setup cannot opt in while the cell has it off
  (`AI_EXPLANATION_DISABLED`).
- The rule result is computed first and never depends on the explanation. The model receives only codes and counts (state, rule set version, rule
  ids, finding codes, severities, counts); never a mandate, agent, person, grant or evidence id, a token, a document or a metadata string. The request
  type has no tools field. The text is accepted only if every rule id, code and number it names is in the result and it has no link or markup;
  otherwise, and on an error, a timeout or no gateway, a fixed template built from the same facts is used. The explanation is kept in the run's
  summary with `authoritative: false` and an `explanation_attached` evidence envelope; the run detail shows it labelled, below the result.
- Gap (needs a product and privacy decision): the only model gateway in the repository is the public assistant's (`backend/assistant`), a separate
  service and image that never imports the platform; the platform image has no model SDK. The explanation follows that gateway's contract (a provider
  drafts from delimited untrusted data, no tools, failures raise) through `ExplanationGateway`, but no production provider is wired:
  `app.state.access_readiness_explainer` is None, so even with both switches on the fixed text is shown until an approved gateway (provider, region,
  budget) is handed in.

### Queue consumer (section 11; section 04 QUEUED -> PREFLIGHT)
- The worker step `access_readiness` (`entrypoints/worker.py`, after `agent_lifecycle`) calls `runs.drain_queued` on the worker's BYPASSRLS
  connection. With `ZID_ACCESS_READINESS_ENABLED` off it returns `off` and reads nothing. On, it takes at most 25 queued runs per pass (oldest
  first, across tenants, `FOR UPDATE SKIP LOCKED` so two workers never take the same run), sets each run's tenant scope and executes it in its
  own savepoint with a full preflight, so a run queued before a pause, a drift or a revocation is denied when it is drained.
- A run whose execution breaks unexpectedly is rolled back and then denied with `DEPENDENCY_UNAVAILABLE` in a second savepoint, so it neither
  stops the other runs nor breaks every later pass. The worker has no model gateway and uses the Q1 usage hook.

### Usage and budget control hook (sections 08 and 12)
- `application/usage.py`: a hook is asked before a run starts and before each step: each read (`read_mandate_metadata`,
  `read_agent_identity_status`), the rule evaluation (`evaluate_rules`), each proposal write (`create_access_review_proposal`) and a model
  request for the explanation (`explain_model_request`). It receives ids and the step name only, no connection.
- The hook that ships (`no_thresholds`) sets no limit and permits. Anything other than exactly `ALLOW` is a denial with
  `BUDGET_POLICY_DENY`: an explicit `DENY`, an error, no answer within 2 seconds (`usage_timeout_seconds`) or any other answer. Each denial is
  a policy decision (`usage_policy.check`, `step:<step>`, DENY) and a `budget_policy_denied` evidence envelope with the step and the cause
  (DENY, ERROR, TIMEOUT, UNKNOWN); the permit at the start is a decision too (`USAGE_PERMITTED`). There is no fallback: at the start the run
  is DENIED, at a read or the rules it is FAILED, at a write it is CANCELED with any proposal it had begun rolled back, and a refused model
  request is never sent (the fixed text, `fallback_reason: BUDGET_POLICY_DENY`).
- Injectable: `app.state.access_readiness_usage_policy` (None by default, which is the Q1 hook); tests hand in their own.
- The thresholds (quotas, spend, schedules, approval limits) are Q2 (D-07) and are not built. The commercial usage reservations
  (`/v1/usage/reservations`) were not reused: they meter billed customer usage per commercial meter, and using them would mean inventing a
  meter and a limit for the agent.

### Observability (section 11)
- Metrics through the platform's governed registry (`zid_telemetry.metrics`, the cell's scrape endpoint):
  `zid_access_readiness_runs_total{state}` (every terminal state, including runs canceled by a pause),
  `zid_access_readiness_denials_total{reason}` (every DENY policy decision, by reason code) and
  `zid_access_readiness_alerts_total{kind,result}`. No tenant, agent, run or person label.
- Log lines of a run read `access readiness run=<run id> trace=<trace id> ...` with codes and counts only; never a token, a document, a metadata
  string or a personal field (the JSON formatter scrubs the message too).
- Two mandatory alerts through `notification.ports.raise_alert`, as the run notification is raised (`application/observe.py`):
  `access_readiness.policy_drift` (HIGH, a task) when a run is stopped by `APPROVAL_DIGEST_MISMATCH` or `POLICY_CHANGED`, at the preflight or
  before a write, folded per agent (`access_readiness.policy_drift:<agent>`), so every run the same drift stops is one alert with a count; and
  `access_readiness.evidence_integrity` (CRITICAL, a task) when a run's evidence chain does not verify on the evidence read or the export,
  folded per run. An alert that cannot be raised never changes the run or the read; it is logged and counted.

### Metadata is data (section 12, "Malicious prompt in metadata")
- `domain/untrusted.py`: after the read, every metadata string (ids, status, references, actions, tools) is checked against a small,
  conservative pattern set (ignore-instructions phrases, role markers, persona switches, requests to call a tool, requests to widen access).
  No detector exists elsewhere in the platform image (the public assistant's patterns are in a separate service), so this is new.
- A match is a policy decision (`metadata.instruction`, `mandate:<id>#<field>`, DENY, `UNTRUSTED_INSTRUCTION_IGNORED`), an
  `untrusted_instruction_marker` evidence envelope (field, rule, digest) and a log line with the same; never the string. A suspicious id is
  referred to by its digest. The rules run on exactly the same facts, so the result does not change.

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
| (WF-AG-03 export) | POST .../runs/{run_id}/evidence:export (purpose required; redacted without review) | access_readiness.read; .review for the full export |
| POST .../{id}/pause | POST /v1/access-readiness/agents/{agent_id}:pause (and `:resume`) | access_readiness.manage |
| GET .../agent-review/{proposalId} | GET /v1/access-readiness/review-proposals/{id}; `:dismiss`, `:assign`, `:handoff` | access_readiness.review (R2) |

Events: `agent.run.policy_denied`, `agent.run.completed`, `agent.review.proposal_created` (proposed-events.yaml), and the existing
`machine.workload.credential_issued`. Audit-only records: `access_readiness.*` and `machine.workload.lease_revoked`. Role matrix routes are mapped in
`route-map.yaml` to the same matrix actions the machine routes use.

## Decisions taken while building
- A refused run is a recorded DENIED run (202 with `state: DENIED` and the reason codes), as the document says publication and run states are
  independent. Whether to answer such a request 403 instead is a console decision.
- A proposal key is tenant, target, rule, rule version and finding code. A dismissed finding therefore stays dismissed until the rule version
  changes. Owner to confirm.
- A handoff is signed with the `set` key ring (JWS, typ `zid-access-review-handoff+jwt`); without a key no handoff is recorded. Owner to confirm which
  key purpose should sign it.
- Execution is inline after the request is queued; a run left queued is taken by the worker's `access_readiness` step (`drain_queued`), and
  each queued run is preflighted again.
- The run's credential is issued at the risk-2 attestation floor the workload-token path uses, so a binding attested only at level 1 passes the
  preflight's "attested" check but gets no credential. Owner decision 2026-11-16: the risk-2 floor stays.

## Tests
- `backend/app/tests/unit/test_access_readiness.py`, `backend/app/tests/unit/test_access_readiness_explain.py` (pure: rules, policy, the explanation
  verifier, injection, no tools, fallbacks; fake gateways only), `backend/app/tests/unit/test_access_readiness_controls.py` (the usage hook
  fails closed, the injection patterns and their redacted markers, metric labels, log lines), `backend/app/tests/unit/test_worker_steps.py`
  (the step is wired after the lifecycle sweep and does nothing while off).
- `backend/app/tests/component/test_access_readiness_pg.py`, `backend/app/tests/component/test_access_readiness_runtime_pg.py` (PostgreSQL with RLS:
  the credential, the notification, the export, the explanation), `backend/app/tests/component/test_access_readiness_controls_pg.py` (the
  worker step: off, bounded, one broken run isolated and denied, a drifted run denied; the usage hook at each step and through `app.state`; the
  drift and integrity alerts and their folding; an instruction in metadata recorded as a redacted marker with the result unchanged).
- Web: the readiness feature's vitest files; `frontend/tests/e2e/readiness-a11y.spec.ts` (the automated part of AT-22: axe WCAG 2.2 AA, 320 px,
  landscape, 200 percent zoom, keyboard tabs) needs the running stack and the mail sink, like the other a11y specs.

## Not built
- Schedules, quotas, spend and approval thresholds (D-07, Q2). Only manual runs exist; `mode: scheduled` is refused. The usage and budget hook
  is in place and fails closed, but sets no threshold.
- Alerting rules on the new metrics in `deploy/observability` (the counters are exposed; no Prometheus rule was added for them).
- A manual screen-reader and zoom pass on real devices (the rest of AT-22); the Playwright spec is automated evidence only.
- A production model gateway for the explanation, a broker-issued bearer token for an external worker and a per-person sponsor notification (the
  gaps above).
- Open decisions that block a production release: D-06 (real release state, API inventory and role schema), D-08 (existing worker substrate or the
  ZoikoVertex runtime), D-09 (official brand assets). Release gates G0 to G4 are not evidence of anything here.
