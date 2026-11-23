# DAARA: the Delegated Authority and Access Readiness agent (complete design, API-only)

Status: **design record of what is built, for the engineering and product owners.** It is not an approval. Sources: ZID-AG-WF-001, CR-091, CR-093, CR-094, CR-105, CR-106, CR-142, CR-146 and the operator guide. Nothing here is certified; the owners' decisions and gates are theirs.

This design uses the **Access Readiness** implementation (module `access_readiness`, console under Admin, Agents, Access readiness). It is the supported one. The older in-module DAARA path (CR-106, `ZID_DAARA_ENABLED`) is retired by default and is not described further here.

## 1. What it is

A first-party reference agent. It reads a company's **mandate metadata** and **agent identity status**, applies six fixed rules, and adds a **review proposal** for a person. It never changes a right, a grant, a mandate or an access. An agent cannot decide a proposal.

It is built **API-only**: its software proof is a registered API client holding a public key. No container image, no attestor and no SPIRE.

## 2. Boundaries (what it will not do)

- It writes exactly one kind of thing: a review proposal. Accepting or opening a proposal changes nothing.
- It reads no names, amounts, documents or purpose text. Its only view of a mandate is the metadata projection: id, state, end date, allowed actions, and the source grant's actions and status.
- It has no free-text input. Metadata is data: every string is checked against a pattern set for planted instructions; a match is ignored, recorded, and referred to by digest, never repeated.
- It decides no identity, company-verification, qualification or biometric question, recovers no account, edits no policy and spends nothing.
- It cannot hold a tool above risk class 1 with a side effect (the API-agent cap, section 4).

## 3. Identity and software proof (the API-only part)

| Layer | What it is |
|---|---|
| Agent principal | A principal of type AI agent, generated, never changes. A display name is only a label. |
| Sponsor and operator | Active members. The sponsor answers for the agent. |
| API client (a build of kind API_CLIENT) | A client id, the key thumbprints (RFC 7638), and the exact audiences it may call, with no wildcards. The platform stores **public keys only** and refuses a private key, a certificate, a key given as an address, an encryption key, a shared secret, a weak curve, RSA under 2048 bits, more than 5 keys or a repeated `kid`. |
| Blueprint release | Its id is exactly `daara`. It comes from the approved client's digest. A second person approves it. |
| Binding | One live binding per agent, in date, naming the approved client. It satisfies the runtime-binding gate. |
| Rotation | A new registration with its own approval. A retired key stops working when its build is revoked. |

The company makes the key pair and keeps the private half. Zoiko never generates, holds or recovers it.

## 4. Tools and the API cap

Two read tools, version `1.0.0`, both risk class 1 with no side effect, hosted by a registered application of the company:

| Tool | Action |
|---|---|
| `read_mandate_metadata` | `mandate.metadata.read` |
| `read_agent_identity_status` | `workload_identity.status.read` |

The third step of the template, writing the proposal, is not a tool on the agent. The platform's own review service writes it, so the agent holds no write.

A cap applies to any API-bound agent: only side effects NONE or REVERSIBLE and risk class 1 or lower (`API_MAX_RISK_CLASS`, `API_ALLOWED_SIDE_EFFECTS`). It is checked at binding, at every gateway call and at credential issue. A tool outside the cap is refused with `API_AGENT_TOOL_NOT_ALLOWED`.

## 5. Lifecycle

1. **Tools** registered and activated (needs `machine.tool.manage`).
2. **API client** registered; a second person approves the build.
3. **Blueprint release `daara`** created; a second person approves it.
4. **Agent** registered: blueprint, sponsor, operator, the two tools. No workload binding (the form refuses one for an API client).
5. **Bind** the approved client.
6. **Four gates** attested: security review, switch-off tested, short-lived credentials, monitoring. The reviewer is not the sponsor, operator, publisher or registrant.
7. **Activation** by a person outside that chain.
8. **Setup** (section 6), **dry run**, **publish approval**.
9. **Runs** and **review**.

Separation rules: the registry refuses the person who registered a thing, or answers for the agent, from approving or activating it. At least three people are involved.

## 6. Setup, dry run and publish

The setup is the agent's scope, shown apart from effective access. The wizard (`SetupWizardPage`, five stages: purpose, scope, runtime, test, publish) has these fields:

| Field | Rule |
|---|---|
| Registered agent | An agent already registered under Agents; the setup creates no identity |
| Display name | A label, up to 80 characters, not unique |
| What it may read | Mandate metadata (the only kind) |
| Records per run | 1 to 500, default 100 |
| Expiry warning window (days) | 1 to 90, default 14 |
| Execution and AI explanation | Manual; the AI flag is off |

The tenant and the region are never typed: the server sets them. The tools are fixed.

- **Draft, then continue:** the server checks the scope.
- **Dry run:** the real rules on synthetic records. It reads nothing live and writes nothing.
- **Publish request:** a separate request approved by a different person, bound to the exact scope digest. Any change to the setup, including turning the AI flag on, voids the approval and needs a new draft, dry run and approval.
- **Pause and resume:** both are `access_readiness.manage` actions on the setup. The company-wide emergency stop is separate (section 10): restoring after it needs two other people.

Schedules exist in the design but are off: they need the agent's approved policy, the company baseline and the platform ceiling to allow them, and the shipped ceiling is off.

## 7. Run: preflight, credential, steps

**Start conditions (a live runtime proof at every run):** the API binding is in date; its client build is approved and not revoked; the release is unchanged; the agent is active and not frozen; the sponsor is active. A run is refused the moment one lapses.

**Preflight (fail closed):** the agent's status, the publish approval still bound to the exact scope digest (`APPROVAL_DIGEST_MISMATCH` or `POLICY_CHANGED` stops a run), the region, the emergency stop, and the usage hook. Then a **scoped lease** is issued: audience `zid:access-readiness:metadata-adapter`, purpose `access_posture_review`, scope of the allowlisted tools, at most 5 minutes. The lease is re-checked before every read and before the write for expiry, audience, purpose, scope, a revoked or rebound binding, a changed security revision and a quarantine. Every exit revokes it.

**Steps:** read mandate metadata; read agent identity status; evaluate the rules; write proposals; build the explanation.

**Run states:** QUEUED, PREFLIGHT, RUNNING, DENIED, COMPLETED, NEEDS_REVIEW, FAILED, CANCELED. A refused run is a recorded DENIED run with its reason codes. A run that could not establish something is NEEDS_REVIEW and marked incomplete, never COMPLETED. A queued run is drained by the worker (at most 25 per pass, `FOR UPDATE SKIP LOCKED`), and its preflight runs again, so a run queued before a pause or revocation is denied when drained. A run that breaks unexpectedly is rolled back and denied with `DEPENDENCY_UNAVAILABLE`, and does not stop others. The run takes an Idempotency-Key.

## 8. The six rules (`daara-rules/1`)

| Rule | Finds |
|---|---|
| R-01 | A mandate that has expired |
| R-02 | A mandate ending within the warning window (set per setup) |
| R-03 | A sponsor not active or unavailable |
| R-04 | A mandate's scope that is not a subset of its source grant |
| R-05 | An evidence gap: the grant has no evidence reference (for example an onboarding grant with no audit record) |
| R-06 | A state it cannot establish: UNKNOWN, blocked, never a pass |

The rules are versioned, take a trusted clock reading, and replay identically. They never depend on the explanation.

## 9. Proposals, review and hand-off

- One proposal per finding. The finding is immutable; only a person's decision changes the proposal.
- A person can **dismiss** or **assign** it, or **open the governed access workflow**, which records a digest-bound **hand-off** (append-only, `executes` is always false). That service asks for its own permissions and a human.
- Reading the review queue needs `access_readiness.review`.

## 10. Governance (the platform plane around it)

**What an Access Readiness run needs from governance: only the emergency stop.** A run refuses to start, and stops before its next page, while a stop covers the agent or the whole company (`EMERGENCY_FROZEN`); a stop that cannot be read refuses the run (`GOVERNANCE_UNAVAILABLE`). The module reads no company baseline, signer slot or governance policy (checked in the module's code). Its own approval is the publish approval in section 6. The rest of this section is the governance plane that guards every agent's **tool-gateway calls** (and the older in-module DAARA path); it is described here because the same console manages it, not because a DAARA run waits for it.

- **Company baseline:** the tools, regions, providers and limits a company allows; it can lower platform limits, never raise them. With no baseline no agent runs. (`PUT /v1/machine/governance/baseline`, needs `agent.governance.manage`.)
- **Signers:** delegates in the SECURITY, AUTHORITY, PRIVACY or FINANCE slot (`agent.governance.delegate`, step-up). Nobody names themselves.
- **Policy version:** tool, resource, purpose, region and expiry; people sign that exact hash; approved is not live until the agent is ACTIVE and the approval is fresh and bound to the hash.
- **Mode (`ZID_AGENT_GOVERNANCE`):** `off`, `shadow` (a refusal goes through and is logged as a would-be refusal) or `enforce` (a refusal is real; no live policy means no calls). The emergency stop is enforced in every mode.
- **Emergency stop:** pause one agent or the whole company; it suspends the agent, cancels running work and releases reservations. Restore needs two other people and fresh checks, and leaves the agent SUSPENDED until it passes its gates again.
- **Quotas:** per-agent and per-company ceilings in `governance.yaml` (pilot values, owner decisions). The usage hook is asked before the run and before each step; anything other than exactly `ALLOW` (a deny, an error, no answer within 2 seconds) is `BUDGET_POLICY_DENY`, with no fallback.

## 11. Evidence

- Run evidence: an envelope chain per run, hash chained, append-only, verified on read. It holds credential issued and revoked, each step, decisions, notification outcome and explanation attached.
- Policy decisions: append-only, one per gate and per denial.
- Governance evidence: one chain per agent plus one for company-level rows, each digest chained to the previous and impossible to edit or delete (a trigger refuses). "Verify the chain" recomputes every digest and names the first broken record.
- **Export:** needs a purpose (`EXPORT_PURPOSE_REQUIRED`, refusals are recorded). References and digests only. Without `access_readiness.review` it is redacted. CSV export of the governance stream needs `agent.audit.export`, is redacted by role, formula-safe, capped at 5000 rows, and is itself audited.

## 12. Notifications, alerts and observability

- Each executed run raises one alert with the run id, state and counts only (a task when NEEDS_REVIEW). It stays in the workspace alert inbox; it is not addressed to a person and sends no email. A failure to notify never changes a run.
- Mandatory alerts: `access_readiness.policy_drift` (HIGH, a task) and `access_readiness.evidence_integrity` (CRITICAL, a task).
- Governance alerts can be acknowledged and routed, have response times (P0 15 min, P1 4 h, P2 24 h, provisional), group when repeated and clear when the condition stops.
- Metrics: runs by state, denials by reason, alerts by kind and result, with no tenant, agent, run or person label. Logs carry codes and counts only.

## 13. Optional AI explanation

Off by default at two switches: the cell's `ZID_ACCESS_READINESS_AI_EXPLAIN` and the setup's own flag (part of the signed digest). The default is fixed text built from the counts, and it is always the fallback. A model, if ever approved, receives only rule ids, codes, severities and counts, with no tools, and its text is accepted only if every rule id, code and number it names is in the result and it carries no link, markup or claim that anything was granted, revoked or run. No provider is wired; the explainer is none, so fixed text is shown. Turning AI on needs Security, Authority, Privacy and Finance.

## 14. Routes and actions

| Purpose | Route | Action |
|---|---|---|
| Create or change the setup | `POST` and `PATCH /v1/access-readiness/agents` | `access_readiness.manage` |
| Dry run | `POST .../dry-runs` | `access_readiness.manage` |
| Publish request, approve | `POST .../publish-requests`, `:approve` | manage; `access_readiness.approve` |
| Run | `POST .../runs` | `access_readiness.manage` |
| Read a run, decisions, evidence | `GET .../runs/{id}`, `/decisions`, `/evidence` | `access_readiness.read` |
| Export evidence | `POST .../runs/{id}/evidence:export` | read; review for the full export |
| Pause, resume | `POST ...:pause`, `:resume` | `access_readiness.manage` |
| Proposals | `GET /v1/access-readiness/review-proposals/{id}`, `:dismiss`, `:assign`, `:handoff` | `access_readiness.review` |
| API agent credential | `POST /v1/machine/api-agent-token` | proves a signed assertion |

The tenant comes from the authenticated principal and is never in a path or body.

## 15. Switches and where it runs

| Setting | Meaning |
|---|---|
| `ZID_ACCESS_READINESS_ENABLED` | Off by default; while off every route answers 404 before authentication and no run executes |
| `ZID_ACCESS_READINESS_REGIONS` | The cells it may run in; empty means none; a cell outside fails closed |
| `ZID_API_AGENTS` | `auto` is on outside production and **off in production**; off means API-agent routes 404, no token and a bound client is no proof |
| `ZID_AGENT_GOVERNANCE` | `off`, `shadow` or `enforce` |
| `ZID_ACCESS_READINESS_AI_EXPLAIN` | Off |

It runs in the cell and the worker under the runtime role that row-level security applies to. It has no separate service (a decision for the CTO, D-08).

## 16. Data (all tenant-scoped, row-level security)

Setup, approval, run, policy decision (append-only), evidence envelope (append-only, hash chained), review proposal and hand-off (append-only), in the `agent` schema under the stream `access_readiness`. Plus the shared agent tables: profile, tool capability, gate attestation, build, blueprint release, API client binding and the one-time assertion id (burned before the verdict).

## 17. People and the console

| Who | Does |
|---|---|
| Owner or Security lead | Company baseline, signers and policy (governance plane; not needed for a DAARA run) |
| Person A (sponsor and operator) | Registers tools, client, release and agent; creates the setup |
| Person B | Approves the client build, the release, and the publish request |
| Person C | Independent security review and activation |
| Reviewer | Decides proposals |

Console: Agents (Register API client, Register agent), Access readiness and its step-by-step page (`/admin/agents/readiness/start`), the setup wizard (purpose, scope, runtime, test, publish), the run page, the review queue and proposal, and Governance (overview, permissions, approvals, limits, signers, monitoring, review).

## 18. Not built, and open decisions

- A separate attested service for the runner (D-08); a gateway grant or token per read (it runs in-process under a lease).
- Schedules, real quotas and spend caps (owner decisions), a real model provider, and the addressing of an alert to the sponsor in person.
- The company baseline now has a console form (Governance, an agent's Permissions, "Set the company baseline"). It covers tools and regions; quotas, AI providers and the schedule switch are kept as they are and are not edited there.
- The Security owner's answers to section C of the decision pack on API-only agents; the cap stays at risk class 1 until they answer.
- Independent execution, a screen-reader pass, and the commercial release register are separate instruments.
