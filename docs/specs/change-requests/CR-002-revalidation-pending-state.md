# CR-002: `REVALIDATION_PENDING` grant state

- **Target:** ZID-MANDATE-001 §6 (AuthorityGrant lifecycle) · **Raised by:** ADR-0003 · **Status:** Open

## Problem
ZID-MANDATE-001 §5.1 says ROLE_CONTINUOUS grants must "revalidate role + current root" after officeholder change, and §5 says they survive "only after" revalidation. The §6 state machine has no state for the period in between.

## Proposed change
Add `ACTIVE → REVALIDATION_PENDING → ACTIVE | SUSPENDED | REVOKED`. While pending: R2/R3 decisions deny with `AUTHORITY_STALE`; the grant cannot be modified, extended or used to issue child grants (ADR-0003 §5 — engineering proposal for ARB ratification).
