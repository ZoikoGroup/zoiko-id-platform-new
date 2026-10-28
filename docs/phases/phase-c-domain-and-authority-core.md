# Phase C: Domain and authority core

> **Gate (Build Documentation Guideline §3):** Locks the objects and authority semantics before database and API implementation.

Back to the [phase index](README.md).

## Spec documents

| # | Doc ID | Title |
|---|---|---|
| 03 | ZID-DOM-001 | Canonical Domain Model & Objects |
| 07 | ZID-AUTHZ-001 | Authorization, Authority Graph & Policy Engine |
| 08 | ZID-MANDATE-001 | Delegated Authority & Mandate Ledger |
| 10 | ZID-ROOT-001 | Organizational Authority & Root of Trust |

## Deliverables and status

Built in slices; each slice is pure domain logic first, then persistence.

| Deliverable | Where | Status |
|---|---|---|
| Authority algebra: 11 dimensions (actions, resources with `type:*` patterns, purposes, data, jurisdictions, tools, value in fixed decimal + currency, count, bytes, assurance, time); `Effective(child) = Effective(parent) ∩ child` | `modules/authority/domain/algebra.py` | Done |
| Property tests: intersection never widens, chains only narrow, child never authorizes what the parent denies, containment reflexive/transitive (AT-02, AUTHZ-03, MAND-01) | `tests/property/test_authority_algebra.py` | Done |
| Grants, roots AR-0…AR-3, chain resolver: cycles, missing parents, cross-tenant hops, stale/revoked/insufficient roots, depth, delegation flags | `modules/authority/domain/grants.py` | Done |
| One complete chain per request, no privilege stitching (AT-03, AUTHZ-04, MAND-04); issuance gate (MANDATE §2.1, §4) | `grants.py`, `tests/unit/test_authority_chains.py` | Done |
| Grantor continuity (DEC-C2): modes incl. CONTRACT_BOUND (CR-001), `REVALIDATION_PENDING` (CR-002), officeholder change, personal-authority cascade (AT-07, MAND-02/03) | `grants.py` | Done (domain) |
| Mandate ledger rules: lifecycle, multi-dimensional budget, reserve/commit/release, idempotency, lease expiry, reconciliation (MAND-08…15, 23, 24) | `modules/mandate/domain/ledger.py`, `tests/property/test_mandate_ledger.py` | Done (domain) |
| Ledger persistence in PostgreSQL: per-module Alembic stream, RLS, writer fencing, balance row lock + SQL limit guard; **10k-concurrency test with 10% crashes** (AT-04, MAND-08/10/11/14/21/22) | `migrations/mandate/`, `mandate/infrastructure/ledger_store.py`, `tests/component/test_mandate_ledger_pg.py` | Done (passes locally; needs CI with PostgreSQL to close AT-04/BG-05) |
| Composite authority (ALL_OF, K_OF_N, ROLE_PAIR, EXTERNAL_CONFIRMATION) and transaction-bound approvals (AUTHZ-15/16, MAND-16/17) | `authority/domain/composite.py` | Done (domain) |
| Sub-agent mandates, allocated child budgets, revoke/pause cascade (MAND-18…20) | `mandate/domain/subagent.py` | Done (domain) |
| Root of trust: three-part test, source competence, per-entity roots, STALE/CONFLICTED, lifecycle events, idempotent replay (ROOT-04…24) | `organization/domain/roots.py` | Done (domain); registry/vLEI adapters come with Phase G integrations |
| Principal, Identifier, Relationship; monotonic revisions; bitemporal history (DM-AT-01/02/03/07/12/13/15) | `principal/domain/principal.py`, `relationship/domain/relationship.py` | Done (domain); authn/verification/credential/consent/evidence objects belong to Phases E–F |

| Persistence for principals, identifiers, organizations, roots, relationships, grants, scopes: RLS + database guards (monotonic revisions, depth must decrease, immutable grant scope, terminal states) | `migrations/{principal,organization,relationship,authority}/`, module `infrastructure/` stores, `tests/component/test_authority_persistence_pg.py` | Done |
| Agent, workload and tool identity: activation gate, credential issuance checks, tool registry, child agents, compromise response (Doc 09, MACH-01…21) | `machine/domain/agents.py` | Done (domain); SPIFFE/SVID issuance itself comes with DEC-F7 in Phase F |
| Cedar policy engine (DEC-C3): releases, platform floor, schema from the action catalog, pre-publication checks, fail-closed evaluation | `policy_runtime/`, `backend/policies/cedar/platform-floor/floor.cedar` | Done |
| SpiceDB projection (DEC-C4): revision checkpoint, consistency by risk, outage fails closed, rebuild from PostgreSQL (AT-05) | `relationship/infrastructure/spicedb.py`, `tests/component/test_spicedb_projection_pg.py` | Done |
| Decision algorithm (AUTHZ §12) over resolved facts, and the fact-gathering service through module ports; end-to-end over PostgreSQL + Cedar | `decision/domain/pdp.py`, `decision/application/pdp_service.py`, `tests/component/test_decision_e2e_pg.py` | Done |
| Revocation cascade over the database: root change / personal authority end -> grants by continuity mode -> mandates revoked or paused; paused mandates can't resume while the source is invalid (AT-07, MAND-02/03/06/20) | `authority/application/cascade.py`, `mandate/infrastructure/mandate_store.py`, `mandate/application/service.py` | Done |
| Mandate terms and sub-mandates persisted; allocations reserved on the parent ledger in the same transaction (MAND-18/19) | `migrations/mandate/versions/0002_mandate_terms.py` | Done |
| Approvals persisted: one vote per person, transaction-bound, append-only (MAND-16/17, AUTHZ-15/16) | `migrations/authority/versions/0002_approvals.py`, `authority/infrastructure/approval_store.py` | Done |
| Agents, blueprints, workload bindings, tool registry persisted; credential checks from stored state (MACH-02/03/09/20) | `migrations/machine/`, `machine/infrastructure/machine_store.py` | Done |
| Policy releases persisted: publish checks, reviewer separation, one ACTIVE per tenant, immutable source (AUTHZ §18) | `migrations/policy_authoring/`, `policy_authoring/infrastructure/release_store.py` | Done |
| Decision records: every decision appended with release, revisions, chain and input commitments (AUTHZ §24) | `migrations/decision/`, `decision/infrastructure/decision_store.py` | Done |
| 80 spec acceptance tests (AUTHZ, MAND, MACH, ROOT, DM-AT) mapped to passing tests; CI guard keeps the mapping honest | `docs/traceability/acceptance-tests.csv`, `test_acceptance_test_status_is_honest` | Done (IN_PROGRESS until the CI report is attached) |
| CI: lint, types, full suite with PostgreSQL and SpiceDB; JUnit report kept as evidence | `.github/workflows/ci.yml` | Done (runs on the first push/PR) |

**Left for later phases by design:** domain events/outbox for grant, mandate and root changes (Doc 08 §23, Phase D event platform; the cascade is called directly until then); registry/vLEI source adapters (Phase G integrations); SPIFFE/SVID issuance (DEC-F7, Phase F); pseudonymous DecisionReceipts (Phase F evidence).

**Owner actions to close Phase C** (engineering can't): ARB ratification of DEC-C1…C5 and of the engineering rules marked "for the ARB" below; spec owners accept CR-001, CR-002, CR-006, CR-007; the first CI run's JUnit report is attached as evidence to AT-02/03/04/05/07, P0-02/03/04/10, BG-04/05.

**Invariants now guarded by tests:** INV-02, 03, 04, 06, 07, 12 and 15 moved from `planned` to `partial`; INV-01 gained a cross-tenant identity test. End-to-end proof still comes from AT-02/03/04/05/07.

### Findings
- **Continuity across modes:** the specs say a child "cannot adopt broader continuity than its parent", but don't order the modes. Engineering's rule (for the ARB): PERSON_BOUND fits under anything; ORGANIZATION_CONTINUOUS admits anything; ROLE_CONTINUOUS and CONTRACT_BOUND are different bases, so neither becomes the other.
- **Currency mismatch:** intersecting value limits in different currencies leaves nothing spendable (ISO `XXX`, zero), rather than picking one side's currency.
- **Bitemporal correction bug (fixed):** the first history implementation superseded any overlapping version, so a narrow correction hid the old fact for all other days. Caught by bug injection; fixed in `377192d`.
- **Data Model alignment:** the ledger needs states and columns ZID-DATA-001 lacks; raised as [CR-006](../specs/change-requests/CR-006-mandate-ledger-columns.md).
- **Root eligibility by risk:** AR-1 roots at most R1, AR-2 at most R2, AR-3 up to R3, AR-0 nothing (AUTHZ §6, DEC-C2 §2).

## Decisions

| ID | Decision | Owner | Status |
|---|---|---|---|
| [DEC-C1](#dec-c1-policy-and-relationship-model) | Policy and relationship model | Authorization Lead + Chief Architect | Proposed |
| [DEC-C2](#dec-c2-grantor-continuity) | Grantor continuity | Authority & Mandate Lead + Legal | Proposed |
| [DEC-C3](#dec-c3-cedar-via-cedarpy-as-the-policy-language-and-engine) | Cedar (via cedarpy) as the policy language and engine | Authorization Lead | Proposed |
| [DEC-C4](#dec-c4-spicedb-as-the-derived-relationship-projection) | SpiceDB as the derived relationship projection | Authorization Lead | Proposed |
| [DEC-C5](#dec-c5-mandate-ledger-using-fenced-conditional-updates-in-postgresql) | Mandate ledger using fenced conditional updates in PostgreSQL | Authority & Mandate Lead | Proposed |

---

### DEC-C1: Policy and relationship model

- **Status:** Proposed
- **Owner:** Authorization Lead + Chief Architect (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Back-End Target Architecture v2.0 baseline decision ADR-02, §10; closes P0-02 (algebra semantics), P0-04
- **Sources:** Back-End Target Architecture §8.2, §10.1–10.4; Authorization spec §6–7, §13–14; System Requirements (AuthZEN, policy-language encapsulation)
- **Implemented by:** DEC-C3 (Cedar engine), DEC-C4 (SpiceDB projection), DEC-D1 (PostgreSQL authority)

#### Context
P0-04: stale graph projections could grant revoked authority. P0-02: without a formal algebra, delegation can amplify privilege or stitch partial grants. The authorization plane needs a fixed external contract, fixed internal semantics and a fixed consistency model.

#### Decision

##### 1. External contract: OpenID AuthZEN Authorization API 1.0
- Policy enforcement points talk to Zoiko iD **only** through AuthZEN 1.0, so customer apps never couple to the internal policy language.
- Zoiko extensions are **namespaced, optional, versioned context and obligation profiles**, never incompatible endpoints.
- Obligations are emitted only to PEPs that have negotiated support for that obligation type.

##### 2. Internal policy semantics: Cedar-class
These semantics are mandatory regardless of engine:
- explicit principal/action/resource
- deny precedence
- schema typing
- bounded, deterministic evaluation
- versioned immutable policy releases
- obligations
- explanation traces
- static analysis for unreachable or contradictory policies

The engine choice is DEC-C3 (Cedar via cedarpy).

##### 3. Authority algebra (enforced before policy evaluation)
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

##### 4. Relationship store and consistency
- **PostgreSQL holds the canonical grants, organizations and relationships.** A Zanzibar-style relationship store is a **revisioned projection** of it, used for low-latency traversal.
- Every material projection update carries a monotonic revision. The decision service records the relation revision it evaluated.
- **R2/R3 requests specify a minimum acceptable revision, or fall back to authoritative state.** A stale projection may deny, but must never grant new privilege.

##### 5. Decision outcomes
`ALLOW` means the request may proceed only if all mandatory obligations are fulfilled. The other outcomes are `DENY`, `STEP_UP`, `APPROVAL_REQUIRED` and `STATE_UNAVAILABLE`. A PEP that can't fulfil a mandatory obligation must not execute. Reason codes are stable machine contracts.

##### 6. Latency budget (architecture budgets, not vendor promises)

| Component | R0/R1 | R2/R3 |
|---|---|---|
| Gateway + authentication context | ≤10 ms | ≤10 ms |
| Relationship/attribute reads | ≤10 ms | ≤15 ms, revision-aware |
| Policy evaluation | ≤10 ms | ≤15 ms |
| Mandate reservation | not normally required | ≤15 ms p95 |
| **Authorization p95** | **<40 ms regional** | **<60 ms regional** |

#### Acceptance
- Property tests:
  - a child can never authorize what its parent denies
  - narrowing a parent never widens a descendant
  - revoking any node invalidates all dependants within the class SLO
  - union-of-partials attacks fail
- AuthZEN conformance tests pass.
- AUTHZ-07: a known-stale projection never produces a new R2/R3 ALLOW.

#### Consequences
The internal engine can be replaced (DEC-C3, DEC-B3) without changing any customer integration.

---

### DEC-C2: Grantor continuity

- **Status:** Proposed
- **Owner:** Authority & Mandate Lead + Legal (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Back-End Target Architecture v2.0 baseline decision ADR-03; referenced by System Requirements ("continuity follows ADR-03 semantics"), Authorization spec AUTHZ-06 and Data Model §AuthorityGrant ("role-issued continuity follows approved ADR semantics")
- **Sources:** Back-End Target Architecture §8.1–8.2; Mandate Ledger spec §5–7; Canonical Domain Model §6.3; Org Authority & Root-of-Trust spec (successor/restoration rules, ROOT-21); UI/UX Wireframes §12.1 step 5

#### Context
When the person who issued a grant leaves office, what happens to the authority they delegated? The specs fix the principle: personal grants die with the grantor, and role-issued grants may survive if the root and role stay valid. Three specs then defer the detailed semantics to this decision.

**Spec inconsistency this decision resolves:**

| Spec | Names used |
|---|---|
| Canonical Domain Model §6.3 | PERSONAL, ROLE-ISSUED, EXTERNAL/CONTRACTUAL |
| Mandate Ledger §5 and UI Wireframes | PERSON_BOUND, ROLE_CONTINUOUS, ORGANIZATION_CONTINUOUS |

Neither list is complete on its own.

#### Decision

##### 1. Canonical continuity modes (one enum, used everywhere)

| `continuity_mode` | Basis of the grant | Domain Model term | On grantor/officeholder change |
|---|---|---|---|
| `PERSON_BOUND` | A specific individual's authority | PERSONAL | Ineffective when that individual's qualifying authority ends, unless a separate surviving basis exists |
| `ROLE_CONTINUOUS` | A verified office/role, whose issuance policy permits continuity | ROLE-ISSUED | Survives officeholder change **only after** current role and root revalidation |
| `ORGANIZATION_CONTINUOUS` | The organization itself, under approved governance (board resolution, signing matrix) | (not named) | Survives personnel change unless the organization, root or policy revokes it |
| `CONTRACT_BOUND` | An external contract or mandate (for example a power of attorney or cross-party delegation) | EXTERNAL/CONTRACTUAL | Follows the governing contract and its root and expiry, not continued account existence |

**Spec change request:** Domain Model §6.3 and the Data Model must adopt these four values.

##### 2. Who may set each mode
- **Default is `PERSON_BOUND`.** Any other mode must be chosen explicitly in the grant wizard (UI §12.1 step 5, "where permitted").
- **`ROLE_CONTINUOUS`** requires:
  - the issuing basis is a role defined in the organization's AuthorityRootAssertion or governance evidence
  - the role definition's issuance policy explicitly permits continuity
  - root assurance **≥ AR-2** for grants usable in R2/R3 actions (AR-1 only for lower-risk tenant administration)
- **`ORGANIZATION_CONTINUOUS`** requires issuance under recorded organizational governance (AR-2 or AR-3 root: board resolution, signing matrix, registry/vLEI).
- **`CONTRACT_BOUND`** requires a contract or external delegation reference with its own validity window and root.
- The mode is **immutable for a grant ID**. Changing it means a superseding grant (Mandate spec §6: material mutation creates a new grant).

##### 3. Continuity is attenuated like every other dimension
*This rule is engineering's derivation from the algebra (child ⊆ parent); the ARB should ratify it explicitly.*

A child grant cannot outlive a break in its chain. If any ancestor is `PERSON_BOUND` to person X, the **whole dependent chain** loses validity when X's qualifying authority ends, whatever mode the descendants declare. Surviving continuity requires every link from the root down to support it.

##### 4. Officeholder change flow (Mandate spec §5.1)
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

##### 5. While revalidation is pending
*This is engineering's proposal where the specs are silent; the ARB should ratify it.*

| Risk class | Behavior during REVALIDATION_PENDING |
|---|---|
| R2/R3 | **Deny** with `AUTHORITY_STALE` (fail closed) |
| R0/R1 | Tenant policy may allow, within a bounded window that the policy defines |
| Any | The grant cannot be modified, extended or used to issue new child grants |

**Revalidation passes** when the role still exists in current governance, the root is ACTIVE and fresh for the action's risk class, and the new officeholder (if one is required) is verified against the root. The successor is recorded in evidence. The grant ID does not change.

**Vacancy:** a role with no current holder does not by itself invalidate `ROLE_CONTINUOUS` grants if the role and root stay valid. The tenant may set a maximum vacancy period, after which grants are SUSPENDED. No new delegations can be issued from a vacant role.

##### 6. Corporate lifecycle events (Org Authority spec)
- **Merger, conversion or successor entity:** the event forces revalidation. Old grants **never silently transfer** to the successor.
- **Restoration:** a restored company does not reactivate historic revoked or expired roots or grants (ROOT-21).
- **Suspension:** a suspended root or grant blocks new authorization but keeps historical decisions reconstructable.

##### 7. Evidence
Every continuity transition (ineffective, revalidation pending, revalidated, suspended, revoked) emits a receipt with actor, authority, reason code, policy release and revisions:
- E2 by default
- E3 when the grant is usable for R3

#### Acceptance
- **AUTHZ-06:** a personal grant ends with grantor authority; role-issued continuity behaves exactly as above.
- **System Requirements continuity tests:** officer removal cascades to all `PERSON_BOUND` descendants and their mandates within the class SLO.
- **Property tests:**
  - no descendant survives a broken `PERSON_BOUND` ancestor
  - revalidation can never widen scope
  - merger events never transfer grants without revalidation

#### Consequences
- The `authz` grant table gains a constrained `continuity_mode` column, plus a `REVALIDATION_PENDING` lifecycle state (spec change request to the Mandate spec §6 state machine).
- The authority module needs a dependency index (root → grants → mandates) so cascades meet the revocation SLO.

---

### DEC-C3: Cedar (via cedarpy) as the policy language and engine

- **Status:** Proposed
- **Owner:** Authorization Lead (name to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** DEC-C1 §2 (engine choice the Authorization spec reserves for an architecture decision)

#### Context
The Authorization spec requires a "Cedar-class typed, analyzable policy language or equivalent": explicit principal/action/resource, deny precedence, schema typing, bounded deterministic evaluation, versioned policy sets and obligations. The final choice is reserved for an architecture decision.

#### Decision
- Use **Cedar**, evaluated through **cedarpy** (Python bindings over the Rust engine).
- Each tenant gets a Cedar schema generated from its action and resource catalog.
- Policy releases are validated (schema typing, static analysis, property tests and conflict checks), then frozen and published as **immutable bundles** keyed by `policy_release_id`. The decision service caches bundles by ID.
- **Authority algebra (attenuation, one-complete-chain rule, continuity, depth/cycle checks) is enforced in the authority module before Cedar is called.** Cedar never composes partial grants.
- Obligations are attached by the decision service from the action catalog and policy annotations, then returned through AuthZEN to PEPs that support them.

#### Consequences
- Fast, analyzable policies, with replay against historic bundles.
- We depend on cedarpy keeping up with Cedar releases. Mitigation: pin versions, and fall back to the Rust engine or cedar-go if needed (DEC-B3).

---

### DEC-C4: SpiceDB as the derived relationship projection

- **Status:** Proposed
- **Owner:** Authorization Lead (name to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** DEC-C1 §4 (Zanzibar-style revisioned projection)

#### Context
P0-04 requires PostgreSQL authority, a Zanzibar-style revisioned projection and consistency tokens, so that R2/R3 can never produce a new ALLOW from stale relationship state.

#### Decision
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

#### Consequences
- Low-latency graph checks, with provable freshness.
- The SpiceDB projection can be rebuilt from Postgres. A rebuild drill is part of the DR tests.

#### Alternatives considered
OpenFGA (similar, weaker consistency-token semantics); graph queries in Postgres (simpler but slower at Stage B/C).

---

### DEC-C5: Mandate ledger using fenced conditional updates in PostgreSQL

- **Status:** Proposed
- **Owner:** Authority & Mandate Lead (name to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** P0-03; Back-End Target Architecture §9.2

#### Context
P0-03: parallel agent actions must never overspend or exceed count or data limits. The ledger must have a home-cell single writer, fencing, and atomic reserve/commit/release with idempotency and reconciliation.

#### Decision
- The ledger runs in the `agent` schema of the home cell's primary database, owned by the MAND module.
- **Reserve** happens in one transaction:
  1. Writer-fence check (`agent.writer_fence` epoch, `FOR SHARE`).
  2. Idempotent reservation insert (`ON CONFLICT (tenant_id, idempotency_key)`).
  3. Conditional `UPDATE mandate_budget … WHERE consumed + reserved + :amount <= limit_amount`. Zero rows means `MANDATE_LIMIT_EXCEEDED`.
  4. Outbox event.
- **Commit and release** are state transitions on the reservation with matching budget arithmetic. An expiry sweeper releases stale reservations. Reconciliation uses the external `execution_id`.
- Cross-cell requests are routed to the home cell. There is never a multi-cell decrement.

#### Consequences
- Row-level locking serializes contention on each budget row, which is correct and needs no SERIALIZABLE retry storms.
- A hot mandate is bounded by single-row throughput. The mitigation, if a design partner needs it, is budget sub-allocation ("slicing") under one parent limit.
- Acceptance: 10k-concurrency test with crash injection; no bound is ever exceeded; full reconciliation afterwards.
