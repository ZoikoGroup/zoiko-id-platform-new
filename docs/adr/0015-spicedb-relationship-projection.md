# ADR-0015: SpiceDB as the derived relationship projection

- **Status:** Proposed
- **Owner:** Authorization Lead (name to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** ADR-0002 §4 (Zanzibar-style revisioned projection)

## Context
P0-04 requires PostgreSQL authority, a Zanzibar-style revisioned projection and consistency tokens, so that R2/R3 can never produce a new ALLOW from stale relationship state.

## Decision
- Canonical relationships live in PostgreSQL (`org.relationship_edge`, owned by REL).
- `rel-projector` consumes outbox events and writes to **SpiceDB** (self-hosted on EKS, with its own Aurora PostgreSQL datastore, per cell). It records `relation_revision → ZedToken` checkpoints.
- Consistency by risk class:

  | Risk | SpiceDB consistency |
  |---|---|
  | R0 | `minimize_latency` |
  | R1 | `at_least_as_fresh` |
  | R2 | `at_least_as_fresh` with the latest committed token; a short bounded wait, then `AUTHORITY_STALE` |
  | R3 | `fully_consistent`; `STATE_UNAVAILABLE` on failure |

- SpiceDB never accepts writes from any source other than the projector, and is never treated as authority.

## Consequences
- Low-latency graph checks, with provable freshness.
- The SpiceDB projection can be rebuilt from Postgres. A rebuild drill is part of the DR tests.

## Alternatives considered
OpenFGA (similar, weaker consistency-token semantics); graph queries in Postgres (simpler but slower at Stage B/C).
