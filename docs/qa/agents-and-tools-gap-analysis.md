# Agents and tools: what is built, and where the gaps are

Date: 2026-11-10. Written for the engineers and product owner designing how agents work. It combines three read-only audits of the code (backend against ZID-MACHINE-001, authority and delegation, consoles and developer experience). Nothing was run for the audits. Where a claim says "verified", it was re-checked in the code directly. The rest is what the audits reported from reading the code.

## What is built

| Area | State |
|---|---|
| Agent identity and lifecycle | Built. Distinct principal, sponsor and owning organization, six states (DRAFT, PENDING_APPROVAL, ACTIVE, SUSPENDED, REVOKED, RETIRED), ten activation gates, kill path that cascades to sub-agents and bindings, a worker sweep that suspends an agent when its sponsor leaves or it expires. |
| Software supply chain | Built. Builds are registered by a person or a pipeline and approved by someone else. A blueprint release comes only from an approved build. The attestor compares the running container to the approved digest. |
| Workload identity | Built. SPIFFE identity, listener with mutual TLS, attestor (levels 0, 1, 2), a 5 minute workload token bound to the certificate. |
| Tool registry and gateway | Built. Tool registry, delegation chain checks, mandate requirement for tools that need one, an approval bound to the exact arguments, the same policy decision as the AuthZEN endpoint, a signed 120 second single-use grant, redemption once at the tool's server with the chain re-checked, audit of every outcome. Python SDK for tool servers. |
| Authority | Built. Authority grants, mandates (scope, value and count limits, window, sub-delegation depth), composite approvals, a mandate ledger with reserve, commit and release that is safe under 10,000 parallel reservations. Leavers cascade to person-bound grants and mandates. |
| Consoles | Built. Agents and tools page (agents, blueprints, tools, builds, pipeline credentials, activation checklist, kill path), workload bindings, mandates, approvals. |

## Gaps that matter for real use (verified unless marked)

1. **A mandate's budget is never consumed by gateway calls (verified).** The gateway and redemption never reserve, commit or release against a mandate, and no catalogued action is marked as consuming one (`consumes_mandate` appears nowhere in the action catalogue). The reservation machinery exists and is safe, but nothing calls it. An agent can make unlimited calls inside a mandate's limits.
2. **A mandate's own value limits are not checked per call (verified that nothing in the decision path reads them).** The decision checks the authority grant's ceiling, not the mandate's per-transaction or total limit. A mandate limited to 100 can still approve a call of 5,000 if the grant allows it.
3. **Redemption does not re-check the policy, the mandate or the approval.** It re-checks the chain and sponsors only. A mandate revoked after signing is still redeemable for up to 120 seconds.
4. **Revoking a binding or quarantining an environment does not stop a token already issued (verified).** The access-token check for a workload only asks whether the principal is ACTIVE. The 5 minute token stays valid. Agent suspension is immediate, so only these two paths have the gap. The specification asks for 2 seconds at the highest risk class.
5. **Credential issuance ignores the agent's expiry and sponsor (verified).** An expired agent, or one whose sponsor left, can still obtain a token until the next sweep (default 10 seconds).
6. **The human gates are clicks and never reset (verified).** Nothing clears the attestations, so suspending and reactivating an agent needs no fresh security review. The "runtime binding" gate is satisfied by any live binding.
7. **Human approval is half-built.** The gateway refuses and returns the exact context to approve, but nothing creates the approval request or notifies an approver. Creating an approval request also appears to need only an authenticated principal (reported, not fully verified).
8. **Attestor keys are cell-wide.** Any pinned attestor key can vouch for any tenant's workload identity. Hardware (level 3) attestation is not built, so a high-risk workload cannot get a token today.
9. **No agent-side client or end-to-end sample.** The SDK is for tool servers only. Nothing shows how an agent gets a token, asks the gateway for a grant and calls a tool. The demo agent only logs a heartbeat.
10. **The console cannot show what an agent did.** No per-agent view of tool calls, grants or refusals (verified that no frontend file references the gateway endpoints). The agent types carry no `expires_at` (verified), so expiry cannot be set or seen in the console. The achieved attestation level and lifecycle breaches are not shown.
11. **Thin definitions.** Tools have no argument schema, owner, expiry or output classification. Blueprints lack source repository, SBOM and model-configuration digest. Tool ids cannot be renamed.
12. **No MCP support, no external-agent support, no cross-tenant delegation.** The specification lists MCP and external agents. Neither is built, and a person acting through agents in two organizations needs separate identities.

## Looks wrong or risky

- The compromise model (`respond()` in the machine domain) has no production caller (verified). Tests exercise a model production code does not use. It also moves DRAFT and PENDING_APPROVAL to SUSPENDED, which the lifecycle forbids.
- A binding registered with the `sha-256:` spelling of a digest can never match an attestation, because the credential path compares exact strings.
- A plain environment mismatch is reported as `ENVIRONMENT_QUARANTINED`.
- The redemption failure path raises before it audits, so a bad grant leaves no trace.
- Error codes and event names differ from the specification (for example `ATTESTATION_INSUFFICIENT` against `ATTESTATION_REQUIRED`).
- A mandate can be created and activated by the same owner (no two-person rule found).
- Plan-feature checks default to shadow mode, so they block nothing until enforcement is set.
- Rotation is documented but has no code. Load is unmet: about 49 token exchanges a second on one process against a 20,000 a second gate.

## Questions only the product owner can answer

1. Should every value-bearing tool call reserve against the mandate (reserve at authorize, commit at redeem, release on expiry), and who owns the unit of account?
2. Is a mandate's limit a hard per-call ceiling, or is the grant's ceiling enough?
3. Is single-tenant delegation final, or should an agent act for a person across organizations?
4. Should a person's suspension, not only departure, pause their agents' mandates, and how fast?
5. Who approves an agent's tool call, how are they told, and is approval per call or per budget?
6. Should the creator of a mandate be barred from activating it?
7. Is a 5 minute workload token acceptable, or does revocation need to be faster?

## Suggested order

1. Close the authority gaps: consume the mandate budget at the gateway and enforce the mandate's own limits (gaps 1 to 3). Everything else assumes this.
2. Close the revocation gaps (4 and 5) and reset the human gates on reactivation (6).
3. Add the agent-side client, an end-to-end sample and the per-agent activity view (9 and 10), so people can build and operate agents.
4. Create the approval request and notify the approver from the gateway (7).
