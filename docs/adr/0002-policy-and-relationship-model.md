# ADR-0002: Policy and relationship model

- **Status:** Proposed
- **Owner:** Authorization Lead + Chief Architect (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Back-End Target Architecture v2.0 ADR-02, §10; closes P0-02 (algebra semantics), P0-04
- **Sources:** Back-End Target Architecture §8.2, §10.1–10.4; Authorization spec §6–7, §13–14; System Requirements (AuthZEN, policy-language encapsulation)
- **Implemented by:** ADR-0014 (Cedar engine), ADR-0015 (SpiceDB projection), ADR-0016 (PostgreSQL authority)

## Context
P0-04: stale graph projections could grant revoked authority. P0-02: without a formal algebra, delegation can amplify privilege or stitch partial grants. The authorization plane needs a fixed external contract, fixed internal semantics and a fixed consistency model.

## Decision

### 1. External contract: OpenID AuthZEN Authorization API 1.0
- Policy enforcement points talk to Zoiko iD **only** through AuthZEN 1.0, so customer apps never couple to the internal policy language.
- Zoiko extensions are **namespaced, optional, versioned context and obligation profiles**, never incompatible endpoints.
- Obligations are emitted only to PEPs that have negotiated support for that obligation type.

### 2. Internal policy semantics: Cedar-class
These semantics are mandatory regardless of engine:
- explicit principal/action/resource
- deny precedence
- schema typing
- bounded, deterministic evaluation
- versioned immutable policy releases
- obligations
- explanation traces
- static analysis for unreachable or contradictory policies

The engine choice is ADR-0014 (Cedar via cedarpy).

### 3. Authority algebra (enforced before policy evaluation)
```
EffectiveAuthority(request, chain) =
    RootValidity ∩ PrincipalStatus ∩ Parent.actions/resources/purposes
  ∩ Child.actions/resources/purposes ∩ TimeWindow ∩ Value/Data/Count limits
  ∩ AssuranceRequirements ∩ JurisdictionPolicy ∩ RiskObligations ∩ MandateCapacity
ALLOW only if ONE complete chain covers the whole request. DENY overrides ALLOW.
```

| Rule | What it means |
|---|---|
| Attenuation | Monotonic on every constrained dimension: child ⊆ parent |
| Delegation depth | ≤ the smallest `max_depth` in the chain |
| Integrity | Cycles are rejected; a grant can't self-authorize its root, modify its governing policy or extend its own expiry |
| No privilege stitching | Partial grants are never unioned; composite authority needs a named composite policy plus every required chain |
| Relationships | Inform policy but do not create authority (`member_of` is context, not signing power) |
| Signals | Risk and signals can tighten or revoke, never grant |

### 4. Relationship store and consistency
- **PostgreSQL holds the canonical grants, organizations and relationships.** A Zanzibar-style relationship store is a **revisioned projection** of it, used for low-latency traversal.
- Every material projection update carries a monotonic revision. The decision service records the relation revision it evaluated.
- **R2/R3 requests specify a minimum acceptable revision, or fall back to authoritative state.** A stale projection may deny, but must never grant new privilege.

### 5. Decision outcomes
`ALLOW` means the request may proceed only if all mandatory obligations are fulfilled. The other outcomes are `DENY`, `STEP_UP`, `APPROVAL_REQUIRED` and `STATE_UNAVAILABLE`. A PEP that can't fulfil a mandatory obligation must not execute. Reason codes are stable machine contracts.

### 6. Latency budget (architecture budgets, not vendor promises)

| Component | R0/R1 | R2/R3 |
|---|---|---|
| Gateway + authentication context | ≤10 ms | ≤10 ms |
| Relationship/attribute reads | ≤10 ms | ≤15 ms, revision-aware |
| Policy evaluation | ≤10 ms | ≤15 ms |
| Mandate reservation | not normally required | ≤15 ms p95 |
| **Authorization p95** | **<40 ms regional** | **<60 ms regional** |

## Acceptance
- Property tests:
  - a child can never authorize what its parent denies
  - narrowing a parent never widens a descendant
  - revoking any node invalidates all dependants within the class SLO
  - union-of-partials attacks fail
- AuthZEN conformance tests pass.
- AUTHZ-07: a known-stale projection never produces a new R2/R3 ALLOW.

## Consequences
The internal engine can be replaced (ADR-0014, ADR-0021) without changing any customer integration.
