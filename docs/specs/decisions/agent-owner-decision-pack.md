# Agent owner decision pack (ZID-AG-WF-001, ZID-AG-WF-002, CR-105, CR-106, CR-093, CR-094)

Status: **PROPOSAL for named owners. Nothing here is approved.** Written 2026-11-14 from Agent Wireframes 01 and 02 (sections 14, 15 and 19), the Global Gap Master and the code as built. Each row gives the question, what the documents and the code already say, the recommended answer, the approver, and the exact change that follows. A recommendation becomes policy only when the named approver signs it.

## How to use this

1. The approver reads their rows, says accept, change or reject, and signs.
2. Engineering applies the "when decided" change. Most are one configuration value or one constant; none needs a redesign.
3. Until signed, the platform stays at its safe defaults: governance `shadow`, schedules off, no AI provider, no budget, no price.

## A. Governance decisions (Wireframe 02, section 19)

| Ref | Question | What exists | Recommendation | Approver | When decided |
|---|---|---|---|---|---|
| AG2-D01 | Adopt DAARA as the first agent, with no privilege change and no purchase | DAARA is built; it only proposes a review; hard-deny list blocks grants, revokes, recovery, KYC and payments in code | **Accept.** It is the narrowest agent that still proves identity, mandate, authorization and evidence | Founder, CTO | Record the signed ADR; no code change |
| AG2-D02 | Ten roles or the existing five | Repo has five roles; the four signer slots are delegates, not roles | **Accept the delegate model.** Do not add roles; add the ten-role catalogue only if IAM wants it, mapped to the same delegate slots | IAM, Security, Product | None if accepted; else extend the role catalogue and route-map |
| AG2-D03 | Sponsor and independent approver assignment | Sponsor must be an active member; requester, sponsor and publisher cannot approve their own request; slots need a named delegate | **Accept as built.** Add: a delegate may not hold two slots on the same request; review delegates quarterly | Security, GRC | Constant plus a test for "one person, one slot per request" |
| AG2-D04 | Pilot quotas against plan limits | Platform ceiling in `governance.yaml` (2 concurrent runs per agent, 5 agents, 20 runs per agent per day, 100 per tenant, 500 records per run, 300 s run) | **Keep the pilot ceiling for the design-partner phase only.** Raise per plan tier through the commercial resolver after four weeks of observed use | Product, SRE, Commercial | Edit `platform_quota_ceiling`; add a per-plan override map when Commercial publishes limits |
| AG2-D05 | Budgets, price source, ledger currency, finance delegates | No budget by default; paid step refused with no cap; currencies USD EUR GBP JPY; price `UNPRICED` | **Keep AI at $0 for the whole pilot.** Ledger currency USD; first cap needs Finance and Security; FinOps names two finance delegates | Global CFO, FinOps | Set the delegates; replace `price_version` when a price source exists |
| AG2-D06 | Approved AI providers, processors, models and regions | No provider ships; explanation is optional, off by default, checked for faithfulness, with a fixed-text fallback | **Approve no provider for the pilot.** DAARA is deterministic and loses nothing. Revisit after a privacy review names a processor and region | Privacy, AI/ML, Security | Register the provider adapter; add region pin to the provider record |
| AG2-D07 | Confirm gateway, event bus, IAM, audit and budget interfaces | All are in the platform and covered by tests; Doc G0 asks for a signed boundary ADR that Zoiko iD, not ZoikoVertex, owns agent identity, authority and evidence | **Sign the boundary ADR now.** The code already assumes Zoiko iD owns them | CTO, Platform | ADR only |
| AG2-D08 | Retention and region of cost records, telemetry and approvals | Evidence is append-only and hash-chained; no deletion path; no region tag on cost records | **Evidence 7 years (security and financial record), telemetry 13 months, approvals 7 years, region equals the tenant's data region.** Legal confirms per jurisdiction | Legal, Privacy | Add retention constants and a region column check; legal-hold flag needs a design |
| AG2-D09 | Kill-switch latency and ownership | Target 5 s for the next call after a freeze; measured with 20 agents in the component test; alert SLAs P0 15 min, P1 4 h, P2 24 h | **Accept 5 s as a target, not an SLO** until production measurement. Security owns the switch; SRE owns the alert rota | SRE, Security | Set SLO after four weeks of production data |
| AG2-D10 | Figma assets, tokens, entitlement labels | Console built with the shared design components; no brand assets used | **Design supplies the final tokens; engineering swaps them.** No code blocker | Product, Design | Token swap |
| AG2-D11 | Customer contract and jurisdiction assurances | Console wording says governed, not certified; matrices say PARTIAL where not proved | **Counsel reviews the wording in section C before any customer sees it** | Legal, Commercial | Wording edits |

## B. DAARA decisions (Wireframe 01, section 15) and release gates

| Ref | Recommendation | Approver |
|---|---|---|
| D-01 DAARA as first agent | Accept (same as AG2-D01) | Founder, CTO |
| D-02 No grant, revoke or verification decisions | Accept; enforced by the hard-deny list | Security |
| D-03 First live write is a human review proposal | Accept; built | Product, Security |
| D-04 AI explain-only, off by default | Accept; keep no provider (AG2-D06) | Privacy, AI/ML |
| D-05 Region-pinned worker, short-lived credentials | Accept the principle. The worker runs in the platform's region; credentials are short-lived (CR-094 for API agents) | SRE, Security |
| D-06 Actual release state and API inventory | **Blocking.** Run the inventory against the live environment and sign it. Engineering can generate the list from the route map and OpenAPI contracts | CTO |
| D-07, D-08 Substrate (this platform or ZoikoVertex) | Recommend this platform for the pilot; decided with AG2-D07 | CTO |
| D-09 Brand assets | With AG2-D10 | Design |

Gates, in order. G0 needs the signed boundary ADR and the inventory (AG2-D07, D-06). G1 needs per-action authorization, tenant isolation, a read-only adapter, short-lived tokens and workload attestation: **built and tested, but an independent tester must run it** (section D). G2 needs the privacy approval: data classes, purpose, legal bases, region, processors, retention (AG2-D06, AG2-D08). G3 needs a signed deployed runner and replay evidence: needs a release pipeline run, not code. G4 needs wording, entitlement, design-partner consent and support path (AG2-D11). **No gate is passed by this document.**

## C. API-agent decisions (CR-093, CR-094)

| Question | Recommendation | Approver |
|---|---|---|
| May an API-only agent exist? | **Yes, for tools that read or can be undone, risk class 2 or lower, attestation capped at 1.** The cap is enforced in code at binding, at the gateway and at token issue | Security owner |
| How does the client get a token? | **Signed one-time assertion** over a registered public key, exact audience, short expiry, single use, lifetime in minutes, refused if the binding is not active or the agent is frozen. No secret is stored. Extends CR-066 | Security owner |
| Is risk class 2 the right ceiling? | Start at 1 for the first month; raise to 2 after review of the evidence | Security owner |
| Does the image path keep a higher attestation floor? | **Yes.** Images stay at the existing floor; API agents never exceed 1 | Security owner |
| Is a verified DPoP proof of the agent's own key enough for assurance Z1 (R0 and R1 only)? | **Yes, as built (CR-094 amendment).** Without it no API-agent tool call can ever be granted. R2 and R3 are untouched (they need Z2 and Z3). Alternative if refused: mark the read-only tool actions `machine_callable` and drop the rule | Security owner |
| May the access readiness agent run on an API client's proof, held at level 1? (CR-142 section 4) | **Yes, as built.** A bound client counts as attestation level 1 only when the token path's own decision passes (agent active and not frozen, binding in date, client build approved and not revoked, release unchanged); it is re-checked at every step; the status says `API_CLIENT`, not a verified image. The agent holds only the two read tools; the platform's review service makes the proposal write. Alternative if refused: keep access readiness on the container path only | Security owner |
| Should an API-client build ever be given a workload binding? (CR-142 section 2) | **No.** An attestor can only inspect an image. The registry refuses it (422 `WORKLOAD_BUILD_IS_API_CLIENT`) and names the API client binding | Security owner (confirm) |
| Should the people who answer for an agent, and the person who registered it, be unable to activate it? (CR-142 section 1) | **Yes**, the same people already barred from the security review (WF-002 section 04, four-eyes). A one-administrator company must name another member as sponsor | Security owner, Product |
| Should the worker run each company's queued agent work as the runtime role? (CR-142 section 8) | **Yes, in every deployment** (`ZID_AGENT_RUN_ROLE=zid_runtime`, membership granted in `01_roles.sql`). The database then confines a run to its company however a query is written; it is the structural answer to the cross-company reads CR-142 section 5 fixed one by one | Security owner, Platform |
| May API-based agents be switched on in production? (CR-144) | **Not until you have answered the rows above.** They ship OFF in production (`ZID_API_AGENTS=auto`); setting `ZID_API_AGENTS=on` is the decision. The registry, governance and the container path do not depend on it, so the release is not blocked by this section | Security owner |

## D. What cannot be closed by code

| Item | What is needed | Suggested owner and evidence |
|---|---|---|
| Counsel review | Customer-facing wording, jurisdiction assurances, retention, "governed, not certified" language, DPIA inputs | Legal; signed wording sheet and a list of jurisdictions covered |
| Independent testing | Someone other than the build team runs the 46 governance and 24 DAARA acceptance rows plus the API-agent rows, with fresh evidence links | QA lead outside engineering; a dated run record per row; Security penetration test of the gateway and token exchange |
| Screen-reader pass | NVDA with Firefox or Chrome and VoiceOver with Safari on the governance, run-detail, review and API-client dialogs; keyboard-only; 200 percent zoom; phone width | Accessibility tester; one recorded script per page, results filed against AC rows AG1-AT22 and the governance equivalents |
| Release pipeline | Signed runner, replay and rollback evidence for G3 | Platform; a signed manifest |
| Design-partner consent | One consenting tenant, support escalation path | Product, Support |

## E. Order of work that unblocks the most

1. Sign the boundary ADR and the API inventory (AG2-D07, D-06). This opens G0.
2. Security owner answers section C; engineering applies the cap and token settings.
3. Privacy and Legal answer AG2-D06, D08 and D11 together. They share one data-handling review.
4. Product and SRE set quotas and the kill-switch target from four weeks of pilot data.
5. Independent testing and the screen-reader pass run on the build that carries all of the above.

## F. Production readiness of the agents: built, designed, or needs a first run

Updated 20 Nov 2026 (CR-142). "Built" has tests; "designed" has a runbook or deploy script and has never run on real machines; "needs people" cannot be done by engineering.

| Area | State | Where it is | What remains, and who |
|---|---|---|---|
| Independence of approvers and activators | Built | CR-064, CR-142 section 1 | Security owner confirms (section C) |
| Live runtime proof at every run and step; API and image proof | Built | CR-142 sections 3 and 4 | Security owner confirms (section C) |
| Isolation between companies for queued work | Built | CR-142 sections 5 and 8 (run as the runtime role, plus company named in every read) | Apply the role grant in each deployment (`deploy/gcp/db/01_roles.sql`); an independent test that two companies cannot see each other's runs |
| DAARA as a worker with its own identity | Built to the extent code can: runs in the worker process as the runtime role under a per-run lease with a live proof | `entrypoints/worker.py`, `leases.py` | A separate worker VM or pod with its own attestation is infrastructure (decision D-08); the design is the same code, deployed as its own service with `ZID_ROLES=worker` |
| Key custody | Designed | `deploy/gcp` (Cloud KMS, HSM keys, OpenBao unsealed by KMS); `deploy/gcp-vm` (master key file, Shamir-sealed OpenBao) | Run `tools/scripts/gcp_kms_smoke.py` on the VM before traffic; Platform and Security |
| External timestamps | Rehearsed for real; a contracted authority is still needed | The rehearsal stack runs its own RFC 3161 authority image (`deploy/local-prod/tsa`) and verifies every token against its root; `tools/scripts/tsa_check.py` proves any authority the same way (accepted under its root, refused under an unrelated one) | Choose and contract a timestamp authority, put its root in `ZID_EVIDENCE_TSA_TRUST_FILE`, run `tsa_check.py` against it; Security |
| HTTPS edge, passkey relying-party id, DNS | Designed | `deploy/gcp/Caddyfile`, `deploy/gcp-vm/Caddyfile`, runbooks | Domain and certificates; Platform |
| Attestor, SPIRE, workload registration | Designed | `deploy/gcp-vm/spire`, `deploy/gcp-vm/attestor`, `docs/runbooks/agent-go-live.md` step 8 | First run on the VMs; level 3 needs a hardware or TEE verifier that does not exist yet |
| Load run at the gate rate | Harness built | `tests/load/machine-token` bound to `contracts/baseline/slo.yaml` | A PERF environment and load generators; SRE |
| Independent acceptance run | Pack and repeatable runner built | `docs/signoff/uat-independent-execution-pack.md`, `docs/qa/agent-independent-test-and-a11y-protocol.md`, `tools/scripts/acceptance_run.py` (a record anyone can repeat and compare row for row) | A tester outside engineering runs it with `--executed-by`; every row stays IN_PROGRESS until the approvers change it |
| Accessibility on a real screen reader | Spec built | `frontend/tests/e2e/agent-governance-a11y.spec.ts` (runs with `E2E_ADMIN_TICKET`) | NVDA and VoiceOver passes by a tester |
| Demonstration on real mandates | Needs people | `docs/operator/agent-governance-guide.md` ("Creating the access readiness agent") | The company's owner and a second person create the mandates, the baseline and the signers, and run it |
