# WF07 (Agents, mandates and runtime authorization): what already exists, what CR-169 added, what is left

Written 25 Nov 2026 for the Zoiko iD product owner. ZID-CP-WF07 v2.0 is explicit: "if repo inspection shows a capability already exists, treat these requirements as verification and reconciliation tasks rather than authorization to build a duplicate service." This is that reconciliation. Findings come from reading the code and running the existing suites, not from the wireframe.

**Headline.** Most of WF07's core already exists and is tested: agent identity and lifecycle, sponsors, attestation, API agents with sender-constrained tokens, authority grants with attenuation and cycle rejection, mandates and an atomic ledger, transaction-bound human approvals, and a tool gateway that is the single enforcement point. What was missing was narrower: no record of what became of an authorized call, no signed receipt of it, no hook between an execution report and the ledger, and no dry run of a stop. CR-169 builds those. Everything else is listed below with its reason.

## Entity and work-package reconciliation

| WF07 item | Already in the repo (evidence) | Status | CR-169 / what is left |
|---|---|---|---|
| A07-01 agent identity record, proof of possession | `agent.agent_profile`, `workload_binding`, attestation (`test_agent_registry_pg`, `test_agent_lifecycle_pg`); DPoP-bound API agents (`test_api_agent_token_pg`) | Built | No `origin_issuer` / `origin_subject` fields for external identities (see A07-11, A07-12) |
| A07-02 sponsor binding | A sponsor must be active for the whole chain; a departed sponsor stops the next call (`test_tool_gateway_pg`, `test_agent_revocation_propagation_pg`) | Built for the sponsor principal | WF06 authority-chain freshness is read through the sponsor's principal status, not a WF06 evidence feed |
| A07-03 mandate canonical bytes and signatures | A mandate's terms are written once and the database refuses to change them | **Built (CR-169)** | The exact terms are written in one fixed form and hashed; the grantor confirms that hash with a fresh strong sign-in and, for a mandate that can spend, an independent second person does too. Zoiko iD signs each confirmation (append-only). With `mandate_signing=enforce` a mandate cannot be activated without them. A platform signature over a confirmed hash: **not a qualified electronic signature**, and no HSM-backed key beyond what the keyring already uses |
| A07-04 delegation attenuation, cycles | `authority.domain.grants` (`resolve_chain`, `validate_child_grant`), `mandate.domain.subagent` (`test_authority_chains`, `test_composite_and_subagents`) | Built | Compiling through the WF-03 policy compiler is not done: the PDP is separate |
| A07-05 runtime PDP and enforcement | Tool gateway (`machine/application/tool_gateway.py`): chain, sponsors, tool registry, mandate coverage, policy decision, bound human approval, one-use grant (`test_tool_gateway_pg`, `test_tool_gateway_redteam_pg`) | Built for the gateway | Only the gateway enforces. No other enforcement adapter exists |
| A07-06 atomic shared ledger | `mandate.domain.ledger` and `ledger_store`: reserve, commit, release, expire, reconcile, idempotency by key and context hash, a writer fence (`test_mandate_ledger_pg`) | Built | Amounts are exact decimals, not integer minor units; no FX policy; no refund semantics. CR-169 wires an execution report to the ledger |
| A07-07 transaction-bound human approvals | `authority/api/approvals.py`: a vote bound to one request hash with a real WebAuthn proof, no self-vote, changed request is a new case (`test_approvals_pg`); the gateway asks for an approval bound to the exact call | Built | The exact payee and amount are not shown by a dedicated approval screen |
| A07-08 emergency revoke and cascade | Suspend and revoke agents, tools and sponsors; every gateway call re-reads them; `security_revision`; freeze control; measured propagation | **Built (CR-169)** for the gateway | A stop by agent, sponsor, blueprint (or version), outside issuer (or one of its keys, which also withdraws those bindings) or the whole company, with a dry run, an epoch, mandates paused and a record of the places it cannot reach. Closed once by someone other than its maker, who can never reinstate. Acknowledgement exists only for the gateway |
| A07-09 receipts and outcomes | Every policy decision leaves a replayable receipt (`decision/application/receipts.py`) | Decision only | **CR-169 builds** the action state machine, outcome report by the tool server, signed append-only receipts, and the unknown-outcome rule |
| A07-10 MCP and native uniform authorization | One gateway is the only path to a grant | **Built (CR-169)** as a thin front | `POST /v1/machine/mcp` (JSON-RPC: initialize, ping, tools/list, tools/call) decides nothing and calls the same gateway as the native route; a test shows the same grant claims. Written against the MCP revision named in the wireframe (2026-07-28), tested with a scripted client, **not a real MCP host**. Resources, prompts and sampling are not offered |
| A07-11, A07-12 Okta and Entra bindings | none before | **Built as evidence (CR-169)**, not certified | An allowlisted provider (exact issuer, audience, subject, pinned public keys) bound to one agent; a presented token is checked on signature, issuer, audience, subject, expiry, lifetime and a one-time id; what it says is kept as an outside claim that never grants anything; a quiet provider is STALE. Okta and Entra are claim-name profiles tested against a local issuer. **Not certified against a real Okta or Entra tenant** (D07) |
| A07-13 independent credential issuance/verifier | The credential module issues VCs for people and organizations | **Built (CR-169)** for agents | A short JWT in the W3C VC 2.0 data model carrying only identifiers, bound to the agent's key. A verifier keeps identity, validity and key possession apart, checks live status of the credential and the agent, accepts a presentation once for one relying party, and always says authority was not evaluated. For the issuing company's relying parties; **not** a public cross-company endpoint; no interoperability test against independent verifiers; no vLEI or qualified-signature claim |
| A07-14 risk observation and detection | WF05 graph covers Verify API sessions only | **Built (CR-169)** modestly | `GET /v1/machine/risk-signals`: unresolved outcomes, provider checks that failed or went quiet, open stops, agents whose sponsor left. Places to look, never a score or a finding, and it lists what it cannot see (denied calls are in the audit trail, not counted here) |
| A07-15 conformance and failure testing | Existing suites above; two ledger tests are known environmental failures here | Partly | Needs the 1,000-call batch and restore tests on a real environment |
| A07-16 policy approval and pilot | n/a | People | Owners D01 to D16 |

## The twelve screens

| Screen | Console today | Left |
|---|---|---|
| .01 Registry and trust inventory | Agents list and detail, status, sponsor, blueprint, build (`admin/agents`) | Search by issuer and region, bulk review as proposals, export with receipt |
| .02 Registration and sponsorship | Register agent and child agent dialogs; Access Readiness | Imported-manifest refusal, two-stage activation wording |
| .03 External identity provider bindings | none | **Console built (CR-169)**: Agent controls, tab Outside providers |
| .04 Mandate studio | Mandates are created from a grant (`/v1/mandates`); "my limits" screens | No studio screen; no signing step |
| .05 Authority chain preflight and simulation | none | API built; **no console screen by design**: preflight is asked by an agent's own workload credential, which a person does not hold |
| .06 Spend and concurrency ledger | Balance read route | No screen of its own; the mandate and its confirmations are in Agent controls, tab Mandate signing |
| .07 Runtime gateway | Gateway routes; governance console | No latency measurement dashboard |
| .08 Human approval | Approval inbox and decision routes | Dedicated exact-transaction disclosure screen |
| .09 Credential and verifier | none | **Console built (CR-169)**: Agent controls, tab Credentials, with a verifier |
| .10 Revocation and dependencies | Suspend and revoke controls | **Console built (CR-169)**: Agent controls, tab Stops, with the dry run first and the closing step |
| .11 Receipts and forensics | Evidence module screens | **Console built (CR-169)**: Agent controls, tab Calls and receipts |
| .12 Governance and assurance | Agent governance console (CR-091) | Connector capability matrix, release gates |

## What CR-169 built

- **Tool action.** The grant the gateway signs is also the start of an action with the same id. States: AUTHORIZED, DISPATCHED (the tool's server redeemed the grant), EXECUTED, FAILED, UNKNOWN, COMPENSATED. UNKNOWN is its own state, never a failure.
- **Who reports.** `POST /v1/machine/tool-actions/{id}:finalize` is accepted only from the server the grant was issued for, only after it redeemed the grant. A different server, or the agent itself, gets "not found". The same report twice changes nothing. A different report after a final outcome is refused.
- **Capacity follows the report.** When a call names the reservation it will spend, EXECUTED commits it, FAILED gives it back, UNKNOWN keeps it held (and the ledger's own rule keeps a high-risk unknown blocked after its lease). A ledger refusal is reported on the receipt, never hidden.
- **Receipts.** A signed, append-only receipt for the authorization and for each reported outcome, carrying only identifiers, digests, states and times. It separates authorized, executed, committed, failed, unknown and compensated. A receipt for an unknown outcome never reads as a success. Receipts verify after a key rotation (the keyring keeps retired public keys) and the database refuses any change to one.
- **Preflight, mandate signing, scoped stops and provider bindings** as described in the rows above.
- **Dry run of a stop.** `GET /v1/machine/agents/{id}/stop-impact` lists the agents, live mandates and unspent grants a stop would reach, and says what it would not undo (actions already running, capacity still held). It changes nothing.

## How preflight works

A rehearsal runs the SAME gateway code and the policy decision in its own simulation mode, then rolls its whole transaction back. So no grant, action, receipt, audit record, decision receipt or billable decision survives; a test counts each table before and after. Capacity is read, never reserved, so "it fits" holds nothing and another spend can still take the room. A question the policy decision could not answer is UNAVAILABLE and is never turned into an ALLOW. One thing is not undone: in-process counters (metrics) are not part of the database.

## Not claimed

Nothing here is a certification, a latency figure, a "complete kill switch" or a parity claim. The gateway is the only enforcement point; calls that do not pass it are not stopped. Receipts record what the gateway decided and what the tool's server reported; they do not prove the real-world effect.

## Still not built (after this round)

- **Certification of the Okta and Entra profiles** against real tenants. It needs a tenant of each.
- **A real MCP host test.** The front is tested with a scripted client only.
- **A public, cross-company credential verifier** with a trust store (D08), and tests against independent verifiers.
- **Acknowledgement from enforcement points other than the gateway.**
- **Rendered-screen tests, accessibility and localization checks** for the console (only the screen logic is unit-tested).
- Canonical-hash signing by a qualified signature provider, if a jurisdiction requires one.
- A screen for mandate creation itself (mandates are created from a grant elsewhere; this page confirms their exact terms).
