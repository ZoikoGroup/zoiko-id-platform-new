# Phase A: Architecture baseline

> **Gate (Build Documentation Guideline §3):** Defines platform invariants, boundaries and gates.

Back to the [phase index](README.md).

## Spec documents

| # | Doc ID | Title |
|---|---|---|
| 01 | ZID-ARCH-002 | Back-End Target Architecture |

## Decisions

| ID | Decision | Owner | Status |
|---|---|---|---|
| [DEC-A1](#dec-a1-deployment-posture-overlay-first) | Deployment posture: overlay first | Chief Architect + Product Lead | Proposed |
| [DEC-A2](#dec-a2-python-313-and-fastapi-for-all-backend-services) | Python 3.13+ and FastAPI for all backend services | Engineering Lead | Proposed |

---

### DEC-A1: Deployment posture: overlay first

- **Status:** Proposed
- **Owner:** Chief Architect + Product Lead (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Back-End Target Architecture v2.0 baseline decision ADR-01, §6; closes P0-01
- **Sources:** Back-End Target Architecture §6.1–6.2, §22.1 (P0-01); Enterprise Overlay spec §1–2; System Requirements (deployment modes)

#### Context
Large enterprises will not replace mature Entra, Okta or Ping estates to adopt a new trust layer (P0-01). Authentication is a replaceable module. The differentiator is the chain from identity and organizational authority to mandate, runtime decision and evidence.

#### Decision

##### Supported deployment modes

| Mode | Use | Identity source | Zoiko iD responsibility |
|---|---|---|---|
| **A: Enterprise Overlay (default)** | Enterprises with incumbent IAM | External IdP/directory stays authoritative for authentication and lifecycle | Identity bridge, authority, mandates, AuthZEN PDP, credentials, continuous signals, evidence |
| **B: Native Identity** | Zoiko platforms and greenfield tenants | Zoiko iD | Full CIAM/workforce identity plus all trust services |
| **C: Credential / Relying-Party Only** | Wallet, EUDI and verification integrations | External issuer/wallet ecosystem | Presentation verification, trust, assurance, policy, evidence |
| **D: Sovereign / Dedicated** | Government or regulated tenants | Customer-selected / federated | Dedicated cell, keys, operators, lockbox, residency controls |

##### Overlay control split (Enterprise Overlay spec)
Within mode A, a tenant uses one of these control splits:

| Split | Meaning |
|---|---|
| **O1: Authorization overlay** | The incumbent IdP authenticates; Zoiko evaluates protected actions |
| **O2: Assurance overlay** | O1 plus step-up, verification, credentials, authority and mandates |
| **O3: Federated trust overlay** | Multiple organizations and IdPs plus Zoiko's authority/evidence plane |
| **O0** | Native mode (mode B) |

**O1/O2 is the default.** O0 is never a forced destination. A tenant may stay overlay-based permanently.

##### Rules
1. Overlay is a **product requirement**, not an integration afterthought. A tenant must be able to:
   - map an external subject to a Zoiko principal
   - ingest lifecycle events (SCIM/JML)
   - evaluate a Zoiko mandate
   - return an AuthZEN decision
   - publish and consume continuous security events

   All of this without copying the customer's password database.
2. **External identity key is `issuer + sub`** (SAML: IdP entityID + NameID). Email, UPN and username are attributes, never keys. Automatic linking on email equality is prohibited for high-assurance accounts.
3. **Directory migration is optional** and separately governed.
4. Provider adapters terminate at a normalized federation/provisioning contract. Policy, authority, session, evidence and principal semantics are **never coded around one vendor's schema**.
5. Onboarding progresses DRAFT → ORGANIZATION_VERIFYING → CONFIGURING → INTEGRATING → **SHADOW → PILOT → ACTIVE**. Shadow mode evaluates without enforcing.

##### Acceptance gate (P0-01)
Using a third-party IdP account and an agent identity, demonstrate an end-to-end action in an existing application where:
- authentication remains external
- Zoiko iD enforces an authority grant and a mandate
- a revocation propagates
- a DecisionReceipt is generated

**No identity migration and no shared database are permitted.**

#### Consequences
- W1 (Overlay Trust Core) is the first customer-facing wave; native identity (W5) comes later.
- The integration plane modules (`federation`, `provisioning`, `integration`, `onboarding`) are on the first-customer critical path.
- The same domain APIs serve all four modes. Sovereign mode is a deployment profile, not a code fork.

#### Alternatives rejected
- **Native-identity-first:** forces rip-and-replace, and fails P0-01.

---

### DEC-A2: Python 3.13+ and FastAPI for all backend services

- **Status:** Proposed
- **Owner:** Engineering Lead (name to be recorded at ratification)
- **Date:** 2026-10-10

#### Context
The specs fix the semantics and data stores but leave the implementation language open. The team has chosen Python. Most services are I/O-bound (database, Kafka, providers, workflows). The decision service has strict latency and throughput targets (see DEC-B3).

#### Decision
- Backend services use **Python 3.13+** with **FastAPI**, **Pydantic v2**, **SQLAlchemy 2 (async) + asyncpg**, **Alembic**, uvicorn + uvloop and orjson.
- Tooling: uv workspaces, ruff, mypy `--strict`, pytest, Hypothesis and import-linter.
- The backend ships as **one modular-monolith package and image** (`zoiko-id-platform`, DEC-D6) of hexagonal modules (architecture §6–7). Module boundaries are enforced by `ports.py` Protocols and architecture tests in CI.
- Domain logic (`domain/`) is pure Python with no I/O imports.
- **The only allowed exception:** the Credential module (CRED) may be built as a separate Kotlin/JVM service if the Python EUDI libraries (mdoc, OID4VP/HAIP) are not mature enough. That would be decided by its own recorded decision at W3 start.

#### Consequences
- One language for the team, fast iteration, and OpenAPI generated from code.
- CPU-heavy paths need care: no blocking work on the event loop, and signing goes to KMS or a thread pool.
- Hot-path performance is gated by DEC-B3.

#### Alternatives considered
- **Go everywhere:** better tail latency, but weaker SAML/EUDI libraries and not the team's choice.
- **Kotlin everywhere:** strong SAML/EUDI libraries, but heavier runtime and not the team's choice.
