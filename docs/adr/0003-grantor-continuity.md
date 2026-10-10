# ADR-0003: Grantor continuity

- **Status:** Proposed
- **Owner:** Authority & Mandate Lead + Legal (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Back-End Target Architecture v2.0 ADR-03; referenced by System Requirements ("continuity follows ADR-03 semantics"), Authorization spec AUTHZ-06 and Data Model §AuthorityGrant ("role-issued continuity follows approved ADR semantics")
- **Sources:** Back-End Target Architecture §8.1–8.2; Mandate Ledger spec §5–7; Canonical Domain Model §6.3; Org Authority & Root-of-Trust spec (successor/restoration rules, ROOT-21); UI/UX Wireframes §12.1 step 5

## Context
When the person who issued a grant leaves office, what happens to the authority they delegated? The specs fix the principle: personal grants die with the grantor, and role-issued grants may survive if the root and role stay valid. Three specs then defer the detailed semantics to this ADR.

**Spec inconsistency this ADR resolves:**

| Spec | Names used |
|---|---|
| Canonical Domain Model §6.3 | PERSONAL, ROLE-ISSUED, EXTERNAL/CONTRACTUAL |
| Mandate Ledger §5 and UI Wireframes | PERSON_BOUND, ROLE_CONTINUOUS, ORGANIZATION_CONTINUOUS |

Neither list is complete on its own.

## Decision

### 1. Canonical continuity modes (one enum, used everywhere)

| `continuity_mode` | Basis of the grant | Domain Model term | On grantor/officeholder change |
|---|---|---|---|
| `PERSON_BOUND` | A specific individual's authority | PERSONAL | Ineffective when that individual's qualifying authority ends, unless a separate surviving basis exists |
| `ROLE_CONTINUOUS` | A verified office/role, whose issuance policy permits continuity | ROLE-ISSUED | Survives officeholder change **only after** current role and root revalidation |
| `ORGANIZATION_CONTINUOUS` | The organization itself, under approved governance (board resolution, signing matrix) | (not named) | Survives personnel change unless the organization, root or policy revokes it |
| `CONTRACT_BOUND` | An external contract or mandate (for example a power of attorney or cross-party delegation) | EXTERNAL/CONTRACTUAL | Follows the governing contract and its root and expiry, not continued account existence |

**Spec change request:** Domain Model §6.3 and the Data Model must adopt these four values.

### 2. Who may set each mode
- **Default is `PERSON_BOUND`.** Any other mode must be chosen explicitly in the grant wizard (UI §12.1 step 5, "where permitted").
- **`ROLE_CONTINUOUS`** requires:
  - the issuing basis is a role defined in the organization's AuthorityRootAssertion or governance evidence
  - the role definition's issuance policy explicitly permits continuity
  - root assurance **≥ AR-2** for grants usable in R2/R3 actions (AR-1 only for lower-risk tenant administration)
- **`ORGANIZATION_CONTINUOUS`** requires issuance under recorded organizational governance (AR-2 or AR-3 root: board resolution, signing matrix, registry/vLEI).
- **`CONTRACT_BOUND`** requires a contract or external delegation reference with its own validity window and root.
- The mode is **immutable for a grant ID**. Changing it means a superseding grant (Mandate spec §6: material mutation creates a new grant).

### 3. Continuity is attenuated like every other dimension
*This rule is engineering's derivation from the algebra (child ⊆ parent); the ARB should ratify it explicitly.*

A child grant cannot outlive a break in its chain. If any ancestor is `PERSON_BOUND` to person X, the **whole dependent chain** loses validity when X's qualifying authority ends, whatever mode the descendants declare. Surviving continuity requires every link from the root down to support it.

### 4. Officeholder change flow (Mandate spec §5.1)
```
Registry/root update: officer removed or replaced
  → AuthorityRootAssertion REVOKED / SUPERSEDED; authority_root_revision++
  → dependent grants:
       PERSON_BOUND            → INEFFECTIVE → revoked
       ROLE_CONTINUOUS         → REVALIDATION_PENDING → revalidate role + current root
       ORGANIZATION_CONTINUOUS → revalidate organizational policy
       CONTRACT_BOUND          → unaffected by personnel change; re-check contract root
  → derived mandates of invalid grants → PAUSED / REVOKED
  → authority/security revisions propagate; PEPs observe within the R1/R2/R3 SLO
```

### 5. While revalidation is pending
*This is engineering's proposal where the specs are silent; the ARB should ratify it.*

| Risk class | Behavior during REVALIDATION_PENDING |
|---|---|
| R2/R3 | **Deny** with `AUTHORITY_STALE` (fail closed) |
| R0/R1 | Tenant policy may allow, within a bounded window that the policy defines |
| Any | The grant cannot be modified, extended or used to issue new child grants |

**Revalidation passes** when the role still exists in current governance, the root is ACTIVE and fresh for the action's risk class, and the new officeholder (if one is required) is verified against the root. The successor is recorded in evidence. The grant ID does not change.

**Vacancy:** a role with no current holder does not by itself invalidate `ROLE_CONTINUOUS` grants if the role and root stay valid. The tenant may set a maximum vacancy period, after which grants are SUSPENDED. No new delegations can be issued from a vacant role.

### 6. Corporate lifecycle events (Org Authority spec)
- **Merger, conversion or successor entity:** the event forces revalidation. Old grants **never silently transfer** to the successor.
- **Restoration:** a restored company does not reactivate historic revoked or expired roots or grants (ROOT-21).
- **Suspension:** a suspended root or grant blocks new authorization but keeps historical decisions reconstructable.

### 7. Evidence
Every continuity transition (ineffective, revalidation pending, revalidated, suspended, revoked) emits a receipt with actor, authority, reason code, policy release and revisions:
- E2 by default
- E3 when the grant is usable for R3

## Acceptance
- **AUTHZ-06:** a personal grant ends with grantor authority; role-issued continuity behaves exactly as above.
- **System Requirements continuity tests:** officer removal cascades to all `PERSON_BOUND` descendants and their mandates within the class SLO.
- **Property tests:**
  - no descendant survives a broken `PERSON_BOUND` ancestor
  - revalidation can never widen scope
  - merger events never transfer grants without revalidation

## Consequences
- The `authz` grant table gains a constrained `continuity_mode` column, plus a `REVALIDATION_PENDING` lifecycle state (spec change request to the Mandate spec §6 state machine).
- The authority module needs a dependency index (root → grants → mandates) so cascades meet the revocation SLO.
