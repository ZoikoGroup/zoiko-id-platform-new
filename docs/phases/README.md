# Build phases

The engineering build follows the nine phases of the **Build Documentation Guideline §3** (Build Dependency Sequence). Each phase file lists its canonical spec documents, its gate, and the architecture decisions that belong to it. The specs stay the upstream authority ([register](../specs/README.md)); a phase file records only the choices the specs leave open or delegate.

Workstreams may overlap only where the upstream contract is stable enough that downstream teams don't have to invent missing rules (Guideline §3).

| Phase | Specs | Gate | Decisions |
|---|---|---|---|
| [A. Architecture baseline](phase-a-architecture-baseline.md) | 01 | Defines platform invariants, boundaries and gates | DEC-A1 overlay-first posture, DEC-A2 Python/FastAPI |
| [B. Requirements contract](phase-b-requirements-contract.md) | 02 | Converts architecture into testable build obligations | DEC-B1 risk classes R0–R3, DEC-B2 design point and unit cost, DEC-B3 decision-service performance gate |
| [C. Domain and authority core](phase-c-domain-and-authority-core.md) | 03, 07, 08, 10 | Locks objects and authority semantics before database and API work | DEC-C1 policy and relationship model, DEC-C2 grantor continuity, DEC-C3 Cedar, DEC-C4 SpiceDB, DEC-C5 mandate ledger |
| [D. Persistence and interfaces](phase-d-persistence-and-interfaces.md) | 04, 05, 16, 21 | Defines source of truth, contracts, service boundaries and event behavior | DEC-D1 Aurora PostgreSQL, DEC-D2 Kafka + outbox, DEC-D3 transaction context, DEC-D4 HTTP/JSON internal calls, DEC-D5 Temporal, DEC-D6 modular monolith |
| [E. Identity protocols and assurance](phase-e-identity-protocols-and-assurance.md) | 06, 11, 12, 13 | Implements authentication, verification, credentials, federation and revocation | DEC-E1 identity protocol stack, DEC-E2 wallet/holder roles |
| [F. Privacy, evidence and security](phase-f-privacy-evidence-and-security.md) | 14, 15, 17, 18, 19 | Closes privacy, audit, cryptographic, operator-trust and sovereignty controls | DEC-F1 evidence privacy, DEC-F2 evidence ledger, DEC-F3 legal roles, DEC-F4 operator access, DEC-F5 keys and secrets, DEC-F6 post-quantum, DEC-F7 workload identity |
| [G. Enterprise integration and platform operations](phase-g-enterprise-integration-and-operations.md) | 20, 22, 23 (+19a) | Deployable into incumbent estates and operable at Tier-1 standards | DEC-G1 cell topology, DEC-G2 AWS, DEC-G3 cell-direct routing, DEC-G4 supply chain, DEC-G5 observability |
| [H. Product surfaces](phase-h-product-surfaces.md) | 24 | Engineering/admin surfaces against already-defined back-end contracts | DEC-H1 Next.js + thin BFF |
| [I. Production proof](phase-i-production-proof.md) | 25 | Executable evidence that the whole system meets architecture and market-entry gates | — (proves A–H) |

**Not placed by the Guideline:** Document 09 (ZID-MACHINE-001, AI Agent, Service & Workload Identity) is missing from the §3 sequence. Its decisions currently sit in DEC-D3 (transaction context) and DEC-F7 (workload identity). The Architecture Chair should assign it a phase.

## How decisions are recorded

Where a spec or the Guideline asks for an "ADR", the decision is recorded in the phase file that owns the subject.

- **ID:** `DEC-<phase><n>` (for example `DEC-C3`). IDs are never reused or renumbered. Always write the `DEC-` prefix, so `DEC-G4` is never confused with release gate G4.
- **Header fields:** Status, Owner (a named person at ratification), Date, Implements (spec section / P0), Sources.
- **Body:** Context, Decision, Acceptance, Consequences, and Alternatives where relevant.
- **Status flow:** Proposed → Accepted → Superseded/Deprecated. Production work relies only on **Accepted** decisions.
- **Going beyond the specs:** where the specs are silent, the decision marks that content as an engineering proposal, so the ARB ratifies it consciously.
- **Spec inconsistency:** the decision names the spec section and raises a [change request](../specs/change-requests/README.md).
- **Changes after acceptance:** add a new decision that supersedes the old one, and mark the old one `Superseded by DEC-…`. Accepted text is not edited in place.

## Owner actions (Phases A–C)

Everything engineering can build for Phases A–C is in the repository. These items can only be closed by the named owners:

| Owner | Action | Closes |
|---|---|---|
| Architecture Review Board | Ratify DEC-A1, A2, B1–B3, C1–C5 with named owners and dates (status → `Accepted`) | BG-01 |
| Architecture Review Board | Ratify the engineering rules where the specs are silent: weakening exceptions lower one level and never take R3 below R2 (B1); continuity ranking (C2); currency mismatch leaves nothing spendable (C1) | DEC-B1/C1/C2 |
| SRE + Security | Sign off Stage A and the R0–R3 SLO floor; set each cell's saturation threshold from load tests | BG-06, BG-08 |
| Spec owners | Accept or amend CR-001, CR-002, CR-006, CR-007 (and CR-003/004/005) | Data Model, Mandate spec alignment |
| Architecture Chair | Assign Document 09 a phase in the Guideline (engineering placed it in C) | Guideline §3 |
| Engineering (on first push) | Attach the first CI run's JUnit report as `evidence` in `gates.yaml` / `acceptance-tests.csv` | AT-02/03/04/05/07, P0-02/03/04/10, BG-04/05 |

Still waiting for later phases: P0-01 overlay proof (federation in E, overlay in G) and the DEC-B3 load run (needs the AuthZEN HTTP endpoint from Phase D).

## Architecture baseline register

Back-End Target Architecture §23.2 gate: *all ten baseline decisions (spec ADR-01 to ADR-10) ratified with a named owner and date.*

| Spec label | Decision | Closes | Owner | Status |
|---|---|---|---|---|
| ADR-01 | [DEC-A1](phase-a-architecture-baseline.md) Deployment posture: overlay first | P0-01 | Chief Architect + Product | Proposed |
| ADR-02 | [DEC-C1](phase-c-domain-and-authority-core.md) Policy and relationship model | P0-02, P0-04 | Authorization + Chief Architect | Proposed |
| ADR-03 | [DEC-C2](phase-c-domain-and-authority-core.md) Grantor continuity | AUTHZ-06 | Authority & Mandate + Legal | Proposed |
| ADR-04 | [DEC-B1](phase-b-requirements-contract.md) Risk classes R0–R3 as platform floor | P0-05 | Security + Product | Proposed |
| ADR-05 | [DEC-D3](phase-d-persistence-and-interfaces.md) Delegated transaction context (no ActAs token) | — | Identity + Machine Identity | Proposed |
| ADR-06 | [DEC-F1](phase-f-privacy-evidence-and-security.md) Evidence privacy and crypto-erasure | P0-06 | Evidence + Privacy/DPO | Proposed |
| ADR-07 | [DEC-F3](phase-f-privacy-evidence-and-security.md) Legal role and establishment model | P0-08 | **Legal + DPO** | Proposed |
| ADR-08 | [DEC-E2](phase-e-identity-protocols-and-assurance.md) Wallet, holder and trust-service roles | — | **Product + Board** | Proposed |
| ADR-09 | [DEC-F6](phase-f-privacy-evidence-and-security.md) Post-quantum migration | — | Security / Crypto | Proposed |
| ADR-10 | [DEC-B2](phase-b-requirements-contract.md) Capacity design point and unit cost | P0-09 | CTO + SRE + Finance | Proposed |

## Inputs still owed by non-engineering owners

These decisions are complete, but need values only these owners can supply before G4:

| Decision | Input needed | Owner |
|---|---|---|
| DEC-F1 | `backup_erasure_window` and crypto-inaccessibility time per retention class (the specs require them but give no numbers) | Privacy / DPO |
| DEC-F3 | Approve the engineering drafts in `backend/config/jurisdiction-packs/` (fill COUNSEL fields) and the role map for launch flows | Legal / DPO |
| DEC-E2 | Board decision on any deferred role (wallet, PID, QTSP) if it's ever pursued | Product / Board |
| DEC-G1 | UK residency DR: the default (no cross-border recovery; tenant accepts a different RTO/RPO) is applied in `backend/config/cells/cells.yaml`. Legal may approve `eu-1` per tenant contract. | Legal + residency owner |

## Spec decision requests deliberately deferred

| Requested by | Topic | Why deferred |
|---|---|---|
| VC/EUDI spec §16 | Trust Service Provider role | The spec defers it; only needed if Zoiko enters qualified trust services (DEC-E2) |
| Org Authority spec | European Business Wallet legal role | Required only once the EU Regulation is final (DEC-E2) |
| Event spec | Payloads over 256 KiB | No exception is needed; a decision is required only if one is ever proposed |

## Spec change requests raised by decisions

| From | Change |
|---|---|
| DEC-C2 | [CR-001](../specs/change-requests/CR-001-continuity-modes.md): adopt the four `continuity_mode` values. [CR-002](../specs/change-requests/CR-002-revalidation-pending-state.md): add a `REVALIDATION_PENDING` grant state. |
| DEC-F6 | Resolved by ZID-SEC-001 §33: ZID-CRYPTO-MI-001 is canonical ([CR-005](../specs/change-requests/CR-005-master-sequence-reconciliation.md)) |

## Former ADR numbers

The decisions were previously kept as ADR files in `docs/adr/` (still in git history). Use this table to translate an old reference:

| Old | New | Old | New | Old | New | Old | New |
|---|---|---|---|---|---|---|---|
| 0001 | DEC-A1 | 0009 | DEC-F6 | 0017 | DEC-D2 | 0025 | DEC-F7 |
| 0002 | DEC-C1 | 0010 | DEC-B2 | 0018 | DEC-G2 | 0026 | DEC-F5 |
| 0003 | DEC-C2 | 0011 | DEC-G1 | 0019 | DEC-G3 | 0027 | DEC-G4 |
| 0004 | DEC-B1 | 0012 | DEC-A2 | 0020 | DEC-C5 | 0028 | DEC-F4 |
| 0005 | DEC-D3 | 0013 | DEC-H1 | 0021 | DEC-B3 | 0029 | DEC-E1 |
| 0006 | DEC-F1 | 0014 | DEC-C3 | 0022 | DEC-D4 | 0030 | DEC-G5 |
| 0007 | DEC-F3 | 0015 | DEC-C4 | 0023 | DEC-D5 | 0031 | DEC-D6 |
| 0008 | DEC-E2 | 0016 | DEC-D1 | 0024 | DEC-F2 | 0000 | this section ("How decisions are recorded") |
