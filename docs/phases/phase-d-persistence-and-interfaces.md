# Phase D: Persistence and interfaces

> **Gate (Build Documentation Guideline §3):** Defines source of truth, contracts, service boundaries and event behavior.

Back to the [phase index](README.md).

## Spec documents

| # | Doc ID | Title |
|---|---|---|
| 04 | ZID-DATA-001 | Data Model & Database Schema |
| 05 | ZID-API-001 | API & Interface |
| 16 | ZID-EVENTS-001 | Event Architecture, Messaging & Webhooks |
| 21 | ZID-SVC-001 | Service Decomposition & Internal Interfaces |

## Decisions

| ID | Decision | Owner | Status |
|---|---|---|---|
| [DEC-D1](#dec-d1-aurora-postgresql-as-the-authoritative-store-schema-per-module-rls) | Aurora PostgreSQL as the authoritative store, schema per module, RLS | Data Platform Lead | Proposed |
| [DEC-D2](#dec-d2-amazon-msk-kafka-with-a-transactional-outbox-and-polling-relay) | Amazon MSK (Kafka) with a transactional outbox and polling relay | Platform Lead | Proposed |
| [DEC-D3](#dec-d3-delegated-transaction-context-with-no-proprietary-actas-token) | Delegated transaction context, with no proprietary "ActAs" token | Identity Lead + Machine Identity Lead | Proposed |
| [DEC-D4](#dec-d4-httpjson-for-internal-service-calls-at-stage-a) | HTTP/JSON for internal service calls at Stage A | Platform Lead | Proposed |
| [DEC-D5](#dec-d5-temporal-for-long-running-workflows-and-sagas) | Temporal for long-running workflows and sagas | Platform Lead | Proposed |
| [DEC-D6](#dec-d6-modular-monolith-deployment) | Modular monolith deployment | Chief Architect + Engineering Lead | Proposed |

---

### DEC-D1: Aurora PostgreSQL as the authoritative store, schema per module, RLS

- **Status:** Proposed
- **Owner:** Data Platform Lead (name to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** DEC-C1 §4 (PostgreSQL remains authority)

#### Context
The Data Model spec makes PostgreSQL authoritative, with tenant isolation, bitemporal facts, RLS as defense in depth, and per-service roles without BYPASSRLS.

#### Decision
- **Aurora PostgreSQL** per cell: one trust-kernel cluster at Stage A, plus separate clusters for SpiceDB, Temporal and evidence metadata.
- One schema per module, each with its own runtime role. Runtime roles never own tables. Migrations use `db_owner_migration`.
- `FORCE ROW LEVEL SECURITY` on tenant tables. `zid-db` sets the tenant and principal with `SET LOCAL` only, inside every transaction, after authentication. The pool resets connection state.
- Conventions: UUIDv7 keys, `tenant_id` on every row, row/security/relation revisions, bitemporal columns, and `numeric(38,9)` for money.
- **Ownership per table, not per schema:** Data Model schema names are kept as namespaces, but the Service spec gives tables in one schema to different services (for example `authz` holds AUTHY grants, POL releases and AUTHZ decisions). `backend/database/table-ownership.yaml` assigns exactly one owning module per table, and DB role grants are generated from it at table level.
- Alembic, **one stream per owning module** (`migrations/<module>/`, version table in the `zid_migrations` schema), expand → migrate → contract. Schemas are created once by `backend/database/schemas/`.
- PgBouncer in transaction mode. `SET LOCAL` is compatible with it.

#### Consequences
- Aurora handles quorum storage across 3 AZs and fenced failover. Writer-epoch fencing above the database is still ours (DEC-C5).
- Stage B introduces tenant sharding within a cell.

---

### DEC-D2: Amazon MSK (Kafka) with a transactional outbox and polling relay

- **Status:** Proposed
- **Owner:** Platform Lead (name to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** DEC-B1 §4 (priority lane for revocation)

#### Context
The Event spec requires a Kafka-compatible replicated log, CloudEvents, AsyncAPI 3.1, a transactional outbox (no dual writes), at-least-once delivery with idempotent consumers, and a priority lane for security signals.

#### Decision
- **Amazon MSK** per cell. Topics are named `zid.<env>.<cell>.<domain>.<class>`, in four lanes: `priority`, `domain`, `evidence` and `integration`.
- Outbox rows are written by `zid-outbox` in the same transaction. The **polling relay** uses `FOR UPDATE SKIP LOCKED`, with the aggregate ID as partition key and an idempotent producer reusing the outbox `event_id`.
- Priority lanes have dedicated relays and consumer pools (target relay latency under 100 ms).
- Consumers deduplicate through an inbox table (`event_id` plus the highest `aggregate_version`).
- Apicurio schema registry with BACKWARD compatibility by default.
- Local development uses Redpanda.

#### Consequences
- Simple and testable at Stage A. Debezium/CDC can replace polling at Stage B without contract changes.

---

### DEC-D3: Delegated transaction context, with no proprietary "ActAs" token

- **Status:** Proposed
- **Owner:** Identity Lead + Machine Identity Lead (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Back-End Target Architecture v2.0 baseline decision ADR-05, §19.4
- **Sources:** Back-End Target Architecture §19.4; API & Interface spec §0.2, §12; AI Agent & Workload Identity spec §8, §20; System Requirements (draft protocols behind adapters)

#### Context
Agents and services act on behalf of people and organizations. Every hop needs the actor, the represented principal, the mandate and the exact transaction. v1.0 proposed a public "ActAs token". Inventing a public token format would lock customers into a proprietary syntax while the IETF and OpenID standards (Transaction Tokens, Identity Chaining) are still drafts.

#### Decision

##### 1. Internal Transaction Context Envelope (TCE)
The TCE is an internal, versioned context object passed between trusted Zoiko enforcement components. **It is not a public bearer token and does not replace OAuth access tokens.**

```json
{
  "tce_version": 1,
  "transaction_context_id": "0199...",
  "executing_principal_id": "0199...",
  "represented_principal_id": "0199...",
  "authority_grant_id": "0199...",
  "mandate_id": "0199...",
  "action": "purchase.create",
  "resource_ref": "supplier/order",
  "purpose_id": "0199...",
  "risk_class": "R3",
  "policy_release_id": "0199...",
  "relation_revision": 882191,
  "security_revision": 184,
  "created_at": "2026-10-06T19:30:00Z",
  "expires_at": "2026-10-06T19:30:20Z",
  "commitment": "sha256:..."
}
```

**Rules:**
- Passed **by reference or as a protected internal envelope**, and bound to an audience/service.
- **Cannot extend authority** beyond the source grant or mandate.
- Contains IDs, revisions and commitments only, never raw evidence or the full authorization graph.
- Short-lived. Long-running tasks stay bound to the original mandate and re-check before each protected effect.

##### 2. Mapping at protocol boundaries

| Boundary | Mapping |
|---|---|
| OAuth 2.0 resource boundary | JWT access token (RFC 9068 profile as applicable); **RFC 8693** `act` semantics; sender constraint (DPoP or mTLS); **RAR** `authorization_details` for transaction detail |
| Internal trusted call chain | TCE / transaction-token adapter behind an internal interface; customers never depend on draft syntax |
| Cross-domain (future) | Identity Chaining / Transaction Token profiles once standardized and production-ready |
| Okta cross-app access (XAA) ecosystem | ID-assertion/XAA adapter that preserves the Zoiko mandate ID and decision-evidence linkage |

##### 3. Machine-actor token rules (AI Agent spec §8)
- **Client credentials:** for a service acting as itself only.
- **Token exchange (RFC 8693):** the preferred way to translate controlled delegation.
- **Client authentication:** `private_key_jwt` or mTLS client auth is preferred over shared secrets.
- **Scope:** a downstream token can never carry broader scopes or audience than the exchange policy permits.
- **Lifetime:** access-token lifetime never exceeds the validity of the source credential, session or mandate.
- **Claims:** may identify the executing workload/agent and the represented principal by opaque IDs.
- **Material represented actions:** authority remains an **online policy check**. A valid token is not authority.

#### Acceptance
- No public API or SDK exposes a Zoiko-proprietary delegation token type.
- Swapping the internal TCE adapter for a standardized transaction-token profile requires no change to public business APIs (API spec acceptance).
- Agent confused-deputy tests (W4) pass: an agent can't use a TCE for a different action, resource, purpose or mandate.

#### Consequences
`zid-context` carries the TCE reference. The `session` module (SES) owns the token exchange. The `decision` module validates the TCE against current revisions for R2/R3.

---

### DEC-D4: HTTP/JSON for internal service calls at Stage A

- **Status:** Proposed
- **Owner:** Platform Lead (name to be recorded at ratification)
- **Date:** 2026-10-10

#### Context
The Service spec allows HTTP/2+JSON, gRPC/Protobuf, or a controlled mix, but makes the contract semantics mandatory: request ID, trace context, server-derived tenant, epoch, caller identity, idempotency key, If-Match, deadline and schema version.

#### Decision
- Internal calls use **HTTP/JSON with FastAPI and OpenAPI-generated Python clients** (httpx with keep-alive pools, orjson).
- They run over Istio ambient mTLS with SPIFFE identities.
- Mandatory headers are carried by `zid-context` (`X-Request-Id`, `traceparent`, `X-Zid-Deadline`, `Idempotency-Key`, `If-Match`, `X-Zid-Schema-Version`). Tenant and caller identity come from the mTLS identity and signed context, never from plain headers.
- In the modular monolith (DEC-D6) **all module-to-module calls are in-process via `ports.py`**. HTTP/JSON applies only between the `cell` and `global` roles, to separate processes, and to any module later extracted (for example the decision service under DEC-B3).
- Revisit gRPC if the W0 benchmark shows serialization or connection overhead on the decision path.

#### Consequences
- One API toolchain (OpenAPI) for both external and internal contracts.

---

### DEC-D5: Temporal for long-running workflows and sagas

- **Status:** Proposed
- **Owner:** Platform Lead (name to be recorded at ratification)
- **Date:** 2026-10-10

#### Context
Tenant onboarding, verification sessions, credential issuance, data-subject requests, erasure, approvals and tenant migration are multi-step sagas. They must be idempotent, survive restarts and use explicit compensation (Service spec §13).

#### Decision
- Use **Temporal** with the Python SDK. Workflows hold workflow state only. Every step calls the authoritative service and records the returned ID and version.
- Temporal is **self-hosted per cell** on its own Aurora cluster, so workflow payloads stay in residency, **Q4 closed by ZID-TENANCY-001 §6, §20:** workflow state is tenant processing, so it must pass the residency coverage matrix and the dependency-sovereignty gate. A managed Temporal service is allowed only for a profile whose gate it passes.
- Workflow payloads contain opaque IDs, not PII.
- Workflow state is never an authorization source. Workflows re-check authority and security before protected effects.

#### Consequences
- Durable, testable sagas without hand-rolled state machines for orchestration. Domain state machines still live in the domain modules.

---

### DEC-D6: Modular monolith deployment

- **Status:** Proposed
- **Owner:** Chief Architect + Engineering Lead (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Supersedes:** the 7+1 deployable-unit packaging in architecture §6 (earlier draft) and the related wording in DEC-A2, DEC-B3 and DEC-D4
- **Implements:** ZID-SVC-001 §4 ("No microservice dogma. An early modular deployment is acceptable.")
- **Sources:** ZID-SVC-001 §4–6, §17, §19, §30; ZID-TENANCY-001 §2–3; ZID-RESILIENCE-001 §0.2, §3

#### Context
The specs define **27 logical services** but explicitly do not require 27 deployables (ZID-SVC-001 §4). The earlier draft grouped them into 8 deployable units. The team prefers a **monolithic architecture**: one codebase, one build, one deployable artifact, which is simpler to develop, test, release and operate in the early waves.

The specs allow this **only as a modular monolith**. ZID-SVC-001 §4 says *"What is not acceptable is a shared mutable domain model that makes future extraction impossible or allows one module to bypass another module's authority rules."* A classic shared-model monolith would also break the Data Model rule of one owner per table.

#### Decision

##### 1. One package, one image
- The whole backend is **one Python package**, `zoiko-id-platform` (`backend/app/src/zid_platform/`), with **29 modules** in `modules/`: the 27 logical services plus `locator` and `discovery`.
- One Docker image (`backend/Dockerfile`), one release, one version.
- The shared `zid-*` platform libraries stay separate workspace packages (mechanics only, no domain logic) and are bundled into the same image.

##### 2. Run modes of the same image
| Setting | Values | Purpose |
|---|---|---|
| `ZID_ROLE` | `cell` (27 modules) or `global` (`locator`, `discovery`) | The global plane must hold no personal data and use its own database (ZID-TENANCY-001 §2), so it runs the same image with only its two modules loaded |
| Process | API, `consumer`, `worker`, `outbox_relay` | `python -m zid_platform.entrypoints.<name>`; background work runs as separate processes of the same image |
| Container default | `supervisor` with `ZID_ROLES=cell,global` | One container runs each listed role as **its own process on its own port** (cell :8000, global :8100), with per-role databases (`ZID_CELL_DATABASE_URL`, `ZID_GLOBAL_DATABASE_URL`). It exits if any role dies, so the runtime restarts the container. Production cells set `ZID_ROLES=cell`. |

The cell role is deployed **once per regional cell** (`uk-1`, `eu-1`). Cells, residency and failover (DEC-G1) are unchanged; the monolith is the unit deployed into each cell.

##### 3. Boundaries kept, and enforced by tests
These rules from the specs stay mandatory inside the monolith. `backend/app/tests/unit/test_architecture.py` fails the build if any is broken:
- **Modules call each other only through `ports.py`.** No module imports another module's `domain/`, `application/`, `infrastructure/` or ORM models.
- **One owner per table** (`backend/database/table-ownership.yaml`), with one migration stream per owning module in `backend/app/migrations/<module>/`.
- **Every module belongs to exactly one plane** (`roles.py`). The global role never loads cell modules.

##### 4. Hot-path protection without separate services
ZID-SVC-001 §19 and ZID-RESILIENCE-001 §3 require that admin, batch and support work never starves R2/R3 decisions. In a monolith:
- **Bulkheads in-process:** separate database connection pools and worker limits for runtime vs. control/integration traffic.
- **Same image, separate replica pools (when load requires):** route runtime paths (`/authz`, `/sessions`, …) and admin paths to different replica sets of the same image behind the gateway. `roles.py` `PLANES` already holds the grouping.
- Background work (consumers, workflows, outbox relay) always runs in separate processes, never in the API process.

##### 5. Extraction stays possible
A module can leave the monolith without touching callers: move `modules/<name>/` and `migrations/<name>/` to a new service, and swap its `ports.py` adapter for an HTTP client generated from `contracts/`. The **only pre-approved extraction** is the `decision` module, if the W0 performance benchmark fails (DEC-B3).

#### Consequences
- **Simpler:** one build, one image (~312 MB), one compose service per role, and one version to release and roll back.
- **Faster module calls:** everything is in-process, which helps the ≤15 ms mandate and ≤60 ms authorization budgets.
- **Shared blast radius inside a cell:** a crash or memory leak in any module affects the whole cell process. Mitigated by the bulkheads and replica pools above, plus N+2 replicas across AZs (DEC-G1).
- **Shared release cadence:** a change to any module ships the whole image. Mitigated by canary rollout per cell (DEC-G4).
- **The boundaries depend on CI.** The architecture tests are a required check, and removing them needs a recorded decision.

#### Alternatives considered
- **8 deployable units (previous draft):** stronger isolation per plane, but more images, pipelines and network hops than the team wants at this stage.
- **Classic shared-model monolith:** rejected; it violates ZID-SVC-001 §4–6 and the Data Model's single table ownership.
- **One deployable per logical service (27):** rejected by ZID-SVC-001 §4 ("no microservice dogma") and far too costly for Stage A.
