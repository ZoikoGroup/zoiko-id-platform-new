# ADR-0014: Cedar (via cedarpy) as the policy language and engine

- **Status:** Proposed
- **Owner:** Authorization Lead (name to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** ADR-0002 §2 (engine choice the Authorization spec reserves for an ADR)

## Context
The Authorization spec requires a "Cedar-class typed, analyzable policy language or equivalent": explicit principal/action/resource, deny precedence, schema typing, bounded deterministic evaluation, versioned policy sets and obligations. The final choice is reserved for an ADR.

## Decision
- Use **Cedar**, evaluated through **cedarpy** (Python bindings over the Rust engine).
- Each tenant gets a Cedar schema generated from its action and resource catalog.
- Policy releases are validated (schema typing, static analysis, property tests and conflict checks), then frozen and published as **immutable bundles** keyed by `policy_release_id`. The decision service caches bundles by ID.
- **Authority algebra (attenuation, one-complete-chain rule, continuity, depth/cycle checks) is enforced in the authority module before Cedar is called.** Cedar never composes partial grants.
- Obligations are attached by the decision service from the action catalog and policy annotations, then returned through AuthZEN to PEPs that support them.

## Consequences
- Fast, analyzable policies, with replay against historic bundles.
- We depend on cedarpy keeping up with Cedar releases. Mitigation: pin versions, and fall back to the Rust engine or cedar-go if needed (ADR-0021).
