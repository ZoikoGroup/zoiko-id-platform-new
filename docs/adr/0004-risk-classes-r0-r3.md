# ADR-0004: Risk classes R0–R3 as the platform floor

- **Status:** Proposed
- **Owner:** Security Lead + Product Lead (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Back-End Target Architecture v2.0 ADR-04, §11; closes P0-05
- **Sources:** Back-End Target Architecture §11.1–11.2; System Requirements §4 (Risk-Class Enforcement Contract); Continuous Evaluation spec; Decision Evidence spec §1.1

## Context
Tokens and sessions can stay valid after identity, credential or authority changes (P0-05). Different actions justify different freshness, latency and failure behavior. Without a fixed floor, each team would pick its own.

## Decision

### 1. The four classes (revocation SLO measured at p99)

| Class | Examples | Enforcement | Revocation SLO | Failure posture |
|---|---|---|---|---|
| **R0 Low** | Public/low-sensitivity reads, non-privileged preferences | Cached policy allowed; asynchronous signals | ≤15 min | Degrade per tenant policy |
| **R1 Standard** | Normal workforce access, ordinary business writes | Short token/session TTLs + CAEP/SSF + revision checks | ≤60 s | Degrade within bounded policy |
| **R2 High** | Sensitive data, privileged workflow, external write, reusable verification | Online authorization; fresh-enough revision; commit re-check for material change | ≤10 s | **Fail closed** if required fresh state is unavailable |
| **R3 Critical** | IdP/MFA change, bulk export, authority delegation, credential signing, high-value agent/admin action | Online decision + mandate reservation + fresh security revision; human approval where required | **≤2 s in-cell; ≤5 s cross-party** where the receiver supports real-time signals | **Fail closed** |

**SLO measurement:** from Zoiko iD's authoritative security-state commit to enforcement by a conforming Zoiko-controlled PEP. Source-system detection delay and third-party receiver behavior are measured separately.

### 2. Where the class comes from
- Every protected action declares its **minimum risk class and minimum evidence class** in the action catalog (`contracts/action-catalog/`). The caller cannot lower either.
- R3 actions are normally E3 evidence, unless a documented architecture or legal decision permits E2.

### 3. Tenants may tighten, never silently weaken
- A tenant may raise a class, shorten TTLs or demand stricter freshness.
- **Weakening R2/R3 is never self-service.** It requires an explicit, recorded exception approved by Product **and** Security. The exception names the action, the tenant, the compensating control and an expiry. It is audited and re-blocks automatically when it expires.
- Platform floors that no exception can change: tenant isolation, key safety, R3 fail-closed and evidence integrity (Readiness plan §22.1).

### 4. Failure semantics
- **R3:** if a PEP cannot establish a fresh enough security revision, new R3 actions fail closed.
- **R2:** may use a tightly bounded cached decision **only** where explicitly approved; otherwise fails closed on missing security state.
- **R0/R1:** may degrade per tenant policy. Revocations are never silently discarded, and stale-signal health is surfaced.
- **Priority lane:** revocations travel on a priority lane separate from analytics and bulk events.
- **Signals can tighten, suspend, require step-up or revoke. They never manufacture authority.**
- **Cross-party subscriptions** have delivery receipts, retry/backoff, replay windows and dead-letter handling.

## Acceptance (P0-05)
Under injected regional failure, measure p99 propagation from committed revocation to each controlled PEP. **Each class must meet its SLO, or the architecture fails production readiness.**

## Consequences
- The action catalog is a required contract artifact from W0.
- Revocation latency per class is a first-class SLI from the first production-like environment (Engineering Brief §8).
