# Architecture Decision Records

Process and numbering: [ADR-0000](0000-record-architecture-decisions.md). Production build relies only on **Accepted** ADRs. Baseline gate (Back-End Target Architecture §23.2): *all ten baseline ADRs ratified with a named owner and date.*

## Architecture baseline: the ten ADRs required by the specs

| ADR | Spec ADR | Title | Closes | Owner | Status |
|---|---|---|---|---|---|
| [0001](0001-deployment-posture-overlay-first.md) | ADR-01 | Deployment posture: overlay first | P0-01 | Chief Architect + Product | Proposed |
| [0002](0002-policy-and-relationship-model.md) | ADR-02 | Policy and relationship model | P0-02, P0-04 | Authorization + Chief Architect | Proposed |
| [0003](0003-grantor-continuity.md) | ADR-03 | Grantor continuity | AUTHZ-06 | Authority & Mandate + Legal | Proposed |
| [0004](0004-risk-classes-r0-r3.md) | ADR-04 | Risk classes R0–R3 as platform floor | P0-05 | Security + Product | Proposed |
| [0005](0005-delegated-transaction-context.md) | ADR-05 | Delegated transaction context (no ActAs token) | — | Identity + Machine Identity | Proposed |
| [0006](0006-evidence-privacy.md) | ADR-06 | Evidence privacy and crypto-erasure | P0-06 | Evidence + Privacy/DPO | Proposed |
| [0007](0007-legal-role-and-establishment.md) | ADR-07 | Legal role and establishment model | P0-08 | **Legal + DPO** | Proposed |
| [0008](0008-wallet-and-holder-role.md) | ADR-08 | Wallet, holder and trust-service roles | — | **Product + Board** | Proposed |
| [0009](0009-post-quantum-migration.md) | ADR-09 | Post-quantum migration | — | Security / Crypto | Proposed |
| [0010](0010-design-point-and-unit-cost.md) | ADR-10 | Capacity design point and unit cost | P0-09 | CTO + SRE + Finance | Proposed |

## Additional architecture decisions requested by the specs

| ADR | Title | Requested by | Status |
|---|---|---|---|
| [0011](0011-cell-topology-and-failure-domains.md) | Cell topology, failure domains, recovery tiers, edge provider | Deployment spec RES-W0, §22 | Proposed |

## Implementation decisions (choices the specs leave open)

| ADR | Title | Implements | Status |
|---|---|---|---|
| [0012](0012-python-fastapi-backend.md) | Python 3.13+ and FastAPI backend | — | Proposed |
| [0013](0013-nextjs-frontend-thin-bff.md) | Next.js frontends with thin BFF | — | Proposed |
| [0014](0014-cedar-policy-engine.md) | Cedar (via cedarpy) policy engine | ADR-0002; Authz spec "final choice is an ADR" | Proposed |
| [0015](0015-spicedb-relationship-projection.md) | SpiceDB relationship projection | ADR-0002 | Proposed |
| [0016](0016-postgresql-aurora-authoritative-store.md) | Aurora PostgreSQL, schema per module, RLS | ADR-0002 | Proposed |
| [0017](0017-kafka-outbox-events.md) | MSK (Kafka) + transactional outbox | ADR-0004 (priority lane) | Proposed |
| [0018](0018-aws-stage-a-cloud.md) | AWS for Stage A, account per cell | ADR-0011 | Proposed |
| [0019](0019-cell-direct-routing.md) | Cell-direct endpoints via discovery | ADR-0011 | Proposed |
| [0020](0020-mandate-ledger-conditional-update.md) | Mandate ledger with fenced conditional updates | P0-03 | Proposed |
| [0021](0021-decision-service-performance-gate.md) | Decision-service performance gate + Go fallback | ADR-0010 | Proposed |
| [0022](0022-internal-transport-http-json.md) | HTTP/JSON internal transport | — | Proposed |
| [0023](0023-temporal-workflows.md) | Temporal for long-running workflows | — | Proposed |

## Deployment architecture

| ADR | Title | Implements | Status |
|---|---|---|---|
| [0031](0031-modular-monolith.md) | **Modular monolith**: one package and image, `cell`/`global` roles, boundaries enforced by architecture tests | ZID-SVC-001 §4 | Proposed |

## Platform security, evidence and operations decisions
The Build Guideline requires an ADR for material decisions on identity, evidence, cryptography and tenancy. These record how the spec rules are implemented.

| ADR | Title | Implements | Status |
|---|---|---|---|
| [0024](0024-evidence-ledger-and-integrity.md) | Evidence ledger and integrity profile (closes Q5) | ADR-0006; Evidence spec §5–12 | Proposed |
| [0025](0025-workload-identity-and-service-mesh.md) | Workload identity (SPIRE) and service mesh (Istio ambient) | CRYPTO-MI-001 §24–29; AI Agent spec §5 | Proposed |
| [0026](0026-key-management-and-secrets.md) | Key management (KMS/CloudHSM) and secrets (OpenBao) | CRYPTO-MI-001 §1–23 | Proposed |
| [0027](0027-software-supply-chain-and-admission.md) | Supply chain (SLSA L3, cosign, CycloneDX) and admission (Kyverno) | P0-07; DevSecOps spec §5–20 | Proposed |
| [0028](0028-operator-access-jit-lockbox.md) | Operator access: zero standing privilege, JIT, lockbox, break-glass | P0-07; Security spec §11–13 | Proposed |
| [0029](0029-identity-protocol-stack.md) | Identity protocol stack and profiles | Auth spec §0.1–0.2 | Proposed |
| [0030](0030-observability-and-telemetry-residency.md) | Observability stack and telemetry residency | Observability spec; Residency spec §18 | Proposed |

## Inputs still owed by non-engineering owners
These ADRs are complete as decisions, but they need values that only these owners can supply before G4:

| ADR | Input needed | Owner |
|---|---|---|
| ADR-0006 | `backup_erasure_window` and crypto-inaccessibility time per retention class (the specs require them to be declared but give no numbers) | Privacy / DPO |
| ADR-0007 | Approve the engineering drafts in `backend/config/jurisdiction-packs/` (fill COUNSEL fields) and the role map for launch flows | Legal / DPO |
| ADR-0008 | Board decision on any deferred role (wallet, PID, QTSP) if it's ever pursued | Product / Board |
| ADR-0011 | UK residency DR: the doc default (no cross-border recovery; tenant accepts a different RTO/RPO) is applied in `backend/config/cells/cells.yaml`. Legal may approve `eu-1` per tenant contract. | Legal + residency owner |

## Spec ADR mentions deliberately deferred

| Requested by | Topic | Why deferred |
|---|---|---|
| VC/EUDI spec §16 | Trust Service Provider role | The spec defers it; only needed if Zoiko enters qualified trust services (see ADR-0008) |
| Org Authority spec | European Business Wallet legal role | Required only once the EU Regulation is final (see ADR-0008) |
| Event spec | Payloads over 256 KiB | No exception is needed; an ADR is required only if one is ever proposed |

## Spec change requests raised by these ADRs

| From | Change |
|---|---|
| ADR-0003 | Canonical Domain Model §6.3 and Data Model: adopt the four `continuity_mode` values (PERSON_BOUND, ROLE_CONTINUOUS, ORGANIZATION_CONTINUOUS, CONTRACT_BOUND). Mandate spec §6: add a `REVALIDATION_PENDING` grant state. |
| ADR-0009 | Resolved by ZID-SEC-001 §33: ZID-CRYPTO-MI-001 is canonical (CR-005) |
