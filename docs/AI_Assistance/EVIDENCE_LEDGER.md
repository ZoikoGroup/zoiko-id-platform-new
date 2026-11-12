# Public assistant: evidence ledger (G0..G4) and open-decision tracker

| Field | Value |
|---|---|
| Date | 2026-11-11 |
| Covers | ZI-CHAT-ADR-006 section 06 gates, ZI-CHAT-ODR-007 OD-001..OD-024 |
| Rule | **Only Approved + Implemented + Verified satisfies a gate** (ODR-007 section 01). Code and tests can supply *Implemented* and part of *Verified*. **Approval is a named human's signature.** This file never marks anything approved. |
| Honest status | **No gate is signed. Every OD is OPEN.** Everything below is the engineering half. |

## Gates

| Gate | Needs (from the documents) | Evidence in the repository | Still needed from people |
|---|---|---|---|
| **G0 Architecture** | Signed ADR-01/02/06/07; architecture diagram; independently verified **network** deny tests | Separate service, image, database and role (DEC-M1/M2). `test_isolation.py` (14): no import of `zid_platform`, no identity library, no identity-shaped setting, only two modules make outbound calls, DB grants confined to its schema. `test_p0_scenarios.py` P0-02: no identity route, forged identity headers/cookies inert, no smuggled fields. Component test: the runtime role is denied platform schemas and cannot alter its own. The `assistant` image **contains no `zid_platform` code** (asserted at build time in the Dockerfile) and runs as a non-root user | CTO + IAM Security signatures on ADR-01/02/06/07. **Network-level** deny matrix from a production-equivalent workload (OD-001/002/003). A C4 deployment diagram. These tests prove the code; the network proof must be run where it is deployed |
| **G1 Knowledge** | Signed ADR-03/04/11; production P01 + P02/P03; withdrawal and unsupported-claim tests | Snapshot digest/signature, production refusal of sample/unsigned content (`test_snapshot.py`, 12). Eligibility engine (`test_eligibility.py`, 10). Verifier (`test_verifier_model.py`, 38). Golden corpus: 28 answers and 18 intents (101). P0-03 (planned capability never asserted), P0-04 (withdrawal in flight and at click time), runtime revocation shared across replicas (`test_revocations.py`) | **The content itself.** Signed P01 (capability catalogue), P02 (sources), P03 (links), P04 (restricted claims), P05 (commercial facts) with named publishers (OD-004..007). Every launch capability is `EVIDENCE_PENDING` today, so the assistant can explain concepts but must not claim availability |
| **G2 Privacy and brand** | Approved ADR-08/09/10/12/13; vendor and data map; contact notice; deletion; naming and assets | Pre-model secret interception, 26 categories (`test_sensitive.py`, 40). P0-01 (no model, store, log or cache holds a canary secret). Consent, DLP, idempotency, encrypted intake, erasure that is never overwritten (`test_intake_delivery.py`, 41). No free-text column in any analytics table. Retention is configuration (RET-01..12) | Privacy/Legal: controller and processor map, vendor terms and region, retention periods, notices, lawful basis, rights process (OD-009..012). Commercial/Support: forms, receivers, routing (OD-013..015). Brand: cleared name, logo, tokens (OD-008). **Intake stays OFF in every market until these are signed** |
| **G3 Function and security** | Signed ADR-05/06/08/13; independent security review; WCAG; corpus; integrations; regression | 341 backend tests, 27 widget tests, 26 proxy tests; ruff and strict mypy clean; contract and API.md checked against the code; Postgres and Valkey component tests in CI. P0-05/06/07: consent and receipt truthfulness; markup, injection and redirect inertness; only sanitised text and approved evidence reach the model | **Independent penetration test** and OWASP LLM corpus sign-off (OD-019/020). **Manual WCAG 2.2 AA** with real assistive technology and a device/browser matrix (OD-022). Load and abuse runs (OD-017). A QA-owned, revisioned acceptance corpus (OD-023). A model vendor decision (OD-010/016); the Claude provider is built and tested against a fake client, **not** run against a live key |
| **G4 Production** | Signed ADR-11/12/14; canary; rollback; kill switch; on-call; provider outage rehearsal | Runtime kill switch and per-feature flags shared across replicas, no deploy (P0-08). Model outage degrades to verified excerpts. Circuit breaker, daily budget. Runbook [public-assistant.md](../runbooks/public-assistant.md). Container image builds from the frozen lockfile | Named on-call and incident owner, **drills** (kill switch, withdrawal, provider outage, restore), canary and rollback rehearsal, SLOs measured from load tests, final ownership (OD-018/021/024) |

## Open decisions (ODR-007)

State of every item is **OPEN**. "Engineering provides" is what the code already supports so that the decision, once taken, is a configuration change and not a rebuild.

| ID | Decision | Owner | Gate | Engineering provides |
|---|---|---|---|---|
| OD-001 | Public service deployment boundary | CTO + IAM Security | G0 | Separate image/process/port; compose profile `assistant`; deny tests. Needs the real network policy |
| OD-002 | Workload identity and authentication | IAM Security | G0 | No identity credential exists in the service; own DB role; secrets by file. Needs the platform's workload identity wiring |
| OD-003 | Tenant and product namespace isolation | CTO + Product | G0 | Own cookie namespace and path, no token bridging, no tenant field accepted |
| OD-004 | P01 capability publisher | Product Director | G1 | Schema, state machine, eligibility; 25 candidates, all unreleased |
| OD-005 | P02 source and locale registry | Knowledge + Product | G1 | Schema, span IDs, review expiry, locale; 17 sample sources |
| OD-006 | P03 destination registry | Product + Web | G1 | Registry, click-time validation, withdrawal; 8 secure links deliberately **suspended** (no URL invented) |
| OD-007 | P04 claims and P05 commercial facts | Product + Counsel + Commercial | G1, G2 | P04 classes and gating; P05 empty by design |
| OD-008 | Name, logo and persona | Founder / Brand | G2 | Descriptive "Zoiko iD Assistant"; no logo invented |
| OD-009 | Controller, processor, purpose map | Privacy + Counsel | G2 | Purpose-limited design; nothing personal collected without it |
| OD-010 | Model vendor, residency, transfers | Privacy + AI Platform | G2, G3 | Provider interface; no fallback to another model; Claude provider built; mock by default |
| OD-011 | Retention, deletion, backups | Privacy + Counsel | G2 | RET-01..12 as settings; purge job; erasure; no raw chat ever stored |
| OD-012 | Rights requests and incident response | Privacy + Security | G2, G3 | Erasure by opaque reference; audit events; no anonymous lookup by email |
| OD-013 | Sales/support form purpose and fields | Commercial + Support | G2 | Minimal field schemas; forms need market + notice + flag |
| OD-014 | Handoff endpoint and acknowledgement | Support Ops + Backend | G2, G3 | Signed webhook adapter, transactional outbox, truthful receipts, log-only default |
| OD-015 | Spam, routing, consent lifecycle | Commercial + Support + Privacy | G2, G3 | Rate limits, DLP, separate marketing record (OFF) |
| OD-016 | Model and deterministic response policy | Head of AI/ML | G3 | Pinned prompt/policy versions; verifier; deterministic-first |
| OD-017 | Inference budget, quotas, reliability | SRE + Finance + AI | G3, G4 | Daily budget, breaker, timeout, token cap; model tier is one setting |
| OD-018 | Regional rollout, failover, shutdown | CTO + SRE | G2, G4 | Kill switch; no regional claim inferred; processing location deferred |
| OD-019 | Threat model, attack corpus, pen-test scope | Head of Digital Security | G0, G3 | [Threat model](../threat-model/public-assistant.md); injection/secret/redirect tests |
| OD-020 | Sensitive-input interception proof | AppSec + Privacy | G2, G3 | Interceptor and P0-01; needs the security-owned corpus and false-positive review |
| OD-021 | Incident escalation, audit, kill test | Head of Digital Security + SRE | G4 | Kill switch, runbook; needs a live drill |
| OD-022 | Accessibility and localisation basis | UX + Accessibility QA | G3 | Widget accessibility tests; en-US only; needs manual AT testing |
| OD-023 | E2E acceptance and regression | QA + Product | G1, G3 | Golden corpus, P0 suite, CI; needs QA-owned revisioned corpus |
| OD-024 | Final ownership, sign-off, monitoring | Founder / Product + CTO | G4 | Metrics (metadata only), health, runbook |

## What would change this ledger

Any change to a vendor, region, claim source, retention period, data flow, route or model re-opens the affected decision and invalidates the affected signatures (ADR-14). The content register is guarded by CODEOWNERS (Product, Legal, Security) for that reason.
