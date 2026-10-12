# Phase B: Requirements contract

> **Gate (Build Documentation Guideline §3):** Converts architecture into testable build obligations.

Back to the [phase index](README.md).

## Spec documents

| # | Doc ID | Title |
|---|---|---|
| 02 | ZID-REQ-001 | System Requirements & NFR |

## Decisions

| ID | Decision | Owner | Status |
|---|---|---|---|
| [DEC-B1](#dec-b1-risk-classes-r0r3-as-the-platform-floor) | Risk classes R0–R3 as the platform floor | Security Lead + Product Lead | Proposed |
| [DEC-B2](#dec-b2-capacity-design-point-and-unit-cost) | Capacity design point and unit cost | CTO + SRE Lead + Finance | Proposed |
| [DEC-B3](#dec-b3-decision-service-performance-gate-in-w0-with-a-go-fallback) | Decision-service performance gate in W0, with a Go fallback | Authorization Lead + SRE Lead | Proposed |

---

### DEC-B1: Risk classes R0–R3 as the platform floor

- **Status:** Proposed
- **Owner:** Security Lead + Product Lead (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Back-End Target Architecture v2.0 baseline decision ADR-04, §11; closes P0-05
- **Sources:** Back-End Target Architecture §11.1–11.2; System Requirements §4 (Risk-Class Enforcement Contract); Continuous Evaluation spec; Decision Evidence spec §1.1

#### Context
Tokens and sessions can stay valid after identity, credential or authority changes (P0-05). Different actions justify different freshness, latency and failure behavior. Without a fixed floor, each team would pick its own.

#### Decision

##### 1. The four classes (revocation SLO measured at p99)

| Class | Examples | Enforcement | Revocation SLO | Failure posture |
|---|---|---|---|---|
| **R0 Low** | Public/low-sensitivity reads, non-privileged preferences | Cached policy allowed; asynchronous signals | ≤15 min | Degrade per tenant policy |
| **R1 Standard** | Normal workforce access, ordinary business writes | Short token/session TTLs + CAEP/SSF + revision checks | ≤60 s | Degrade within bounded policy |
| **R2 High** | Sensitive data, privileged workflow, external write, reusable verification | Online authorization; fresh-enough revision; commit re-check for material change | ≤10 s | **Fail closed** if required fresh state is unavailable |
| **R3 Critical** | IdP/MFA change, bulk export, authority delegation, credential signing, high-value agent/admin action | Online decision + mandate reservation + fresh security revision; human approval where required | **≤2 s in-cell; ≤5 s cross-party** where the receiver supports real-time signals | **Fail closed** |

**SLO measurement:** from Zoiko iD's authoritative security-state commit to enforcement by a conforming Zoiko-controlled PEP. Source-system detection delay and third-party receiver behavior are measured separately.

##### 2. Where the class comes from
- Every protected action declares its **minimum risk class and minimum evidence class** in the action catalog (`contracts/action-catalog/`). The caller cannot lower either.
- R3 actions are normally E3 evidence, unless a documented architecture or legal decision permits E2.

##### 3. Tenants may tighten, never silently weaken
- A tenant may raise a class, shorten TTLs or demand stricter freshness.
- **Weakening R2/R3 is never self-service.** It requires an explicit, recorded exception approved by Product **and** Security. The exception names the action, the tenant, the compensating control and an expiry. It is audited and re-blocks automatically when it expires.
- Platform floors that no exception can change: tenant isolation, key safety, R3 fail-closed and evidence integrity (Readiness plan §22.1).

##### 4. Failure semantics
- **R3:** if a PEP cannot establish a fresh enough security revision, new R3 actions fail closed.
- **R2:** may use a tightly bounded cached decision **only** where explicitly approved; otherwise fails closed on missing security state.
- **R0/R1:** may degrade per tenant policy. Revocations are never silently discarded, and stale-signal health is surfaced.
- **Priority lane:** revocations travel on a priority lane separate from analytics and bulk events.
- **Signals can tighten, suspend, require step-up or revoke. They never manufacture authority.**
- **Cross-party subscriptions** have delivery receipts, retry/backoff, replay windows and dead-letter handling.

#### Acceptance (P0-05)
Under injected regional failure, measure p99 propagation from committed revocation to each controlled PEP. **Each class must meet its SLO, or the architecture fails production readiness.**

#### Consequences
- The action catalog is a required contract artifact from W0.
- Revocation latency per class is a first-class SLI from the first production-like environment (Engineering Brief §8).

---

### DEC-B2: Capacity design point and unit cost

- **Status:** Proposed
- **Owner:** CTO + SRE Lead + Finance (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Back-End Target Architecture v2.0 baseline decision ADR-10, §17; closes P0-09
- **Sources:** Back-End Target Architecture §17–17.1; System Requirements §5; Deployment Topology spec §13–13.1; Data Model §13.1; Readiness plan §26 (performance gate)

#### Context
P0-09: without a design point, SLOs can't be tested and the product can't be priced. The external gap analysis proposed a very large envelope. That is useful as a ceiling, but must not become an uncosted day-one requirement.

#### Decision

##### 1. Staged design points

| Stage | Principals | Authz decisions/s global | Per-cell peak | Token issuance/s | R2/R3 receipts/s |
|---|---|---|---|---|---|
| **A: Launch / first enterprise (ratified by this decision)** | 10M | 20,000 | 5,000 | 10,000 | 2,000 |
| B: Enterprise scale | 100M | 250,000 | 50,000 | 25,000 | 10,000 |
| C: Strategic envelope | 500M | 1,000,000 | 200,000 | 100,000 | 50,000 |

- **Stage A is the current engineering design point.**
- **Stage C is a no-rewrite target, not a provisioning commitment.** IDs, APIs and partitioning must never block evolution to it.
- **Moving to B or C requires** a signed commercial forecast, load-test evidence and a unit-cost review.

##### 2. Qualification
- Each stage must **sustain 2× its design point** long enough to expose saturation, rebalancing and failover behavior, while meeting the class SLOs.
- Tests report p50/p95/p99 with cold-cache, failure and noisy-neighbor cases.
- Load tests include skewed tenant traffic, hot mandate rows, cache loss, dependency latency and AZ evacuation.

##### 3. Headroom and admission
- Normal peak keeps **≥30% cell compute headroom** and the ability to survive the loss of one AZ.
- A cell **stops accepting new tenant placements** before projected one-AZ-loss capacity passes the saturation threshold.
- Autoscaling does not replace database, broker and KMS quota planning.

##### 4. Required unit economics (measured per cell and tenant tier)
- Cost per million AuthZEN decisions, by risk class
- Cost per million token issuances/exchanges
- Cost per million compact and R3 receipts, including storage retention
- Cost per active principal and per active organization per month
- Cost per verification/credential presentation, with and without provider fees
- Cost per SSF event transmitted/received
- Minimum annual cost of a dedicated/sovereign cell

##### 5. Pricing gate
**No public enterprise pricing is finalized** until Stage A unit costs are measured in a production-representative environment and the largest loss-making workload is known.

#### Acceptance (P0-09)
- The 2× Stage A test meets the latency and reliability targets.
- Internal unit costs are published.

#### Consequences
- DEC-B3 (decision-service performance gate) is the first instance of this qualification.
- The METER module emits the cost-attribution meters from W1.

---

### DEC-B3: Decision-service performance gate in W0, with a Go fallback

- **Status:** Proposed
- **Owner:** Authorization Lead + SRE Lead (name to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** DEC-B2 §2 (2x qualification)

#### Context
NFR targets: authorization p95 under 40 ms (R0/R1) and under 60 ms (R2/R3), 5k decisions/s per cell, qualified at 2×. Python handles I/O well but has per-process CPU limits (the GIL).

#### Decision
- Build the real decision path in Python during W0 (FastAPI → SIG + SpiceDB + AUTHY + cedarpy + PG decision record).
- Load-test it with k6 at **10k decisions/s per cell for a 1-hour soak**.
- **Pass:** p95 targets met, p99 within the agreed bound, error rate under 0.01%, and cost per million decisions within the agreed envelope.
- **If it fails:** tune Python (workers, pools, batching), then profile and fix the hot spot. Only if both fail, **extract the `decision` module from the monolith into its own service and rewrite that path in Go (the one sanctioned extraction under DEC-D6)** behind the unchanged AuthZEN and internal contracts.
- Contracts for this service are frozen in OpenAPI before W1, so a rewrite is invisible to callers.

#### Consequences
- The language risk is settled with measurements early, before W1 builds on top of it.
