# Zoiko iD — Repository Structure

| Field | Value |
|---|---|
| Document ID | ZID-REPO-001 |
| Version | 0.2 (Draft) |
| Relates to | [ZID-SAD-001 §6–7, §15, §19](zoiko-id-architecture.md) |

One monorepo holds the whole platform. Server code lives in `backend/`, user-interface code in `frontend/`. Anything both sides (or customers) use stays at the root: contracts, SDKs, deployment, system tests, local stack and docs.

---

## 1. Top level

```
zoiko_ids/
├── backend/        Python / FastAPI (own uv workspace)
├── frontend/       Next.js / TypeScript (own pnpm + Turborepo workspace)
├── contracts/      OpenAPI, AsyncAPI, error codes, action catalog (source of truth for interfaces)
├── sdks/           PEP SDKs for customer apps + Envoy ext-authz plugin
├── deploy/         OpenTofu, Helm, Argo CD, Kyverno admission policies
├── tests/          System-level suites (e2e, concurrency, isolation, load, chaos, conformance, security)
├── local/          Local dev stack config + synthetic seed data
├── tools/          Code generation and repo scripts
├── docs/           Specs, architecture, ADRs, traceability, threat model, runbooks
├── .github/        CI workflows, CODEOWNERS
└── README.md · .editorconfig · .gitignore · .gitattributes (Git LFS for .docx/.pdf)
```

**Why `contracts/` sits at the root:** the backend implements the contracts, while the frontend and the SDKs consume clients generated from them. Putting the contracts inside either side would make one side depend on the other's folder.

---

## 2. `backend/`: modular monolith (ADR-0031)

```
backend/
├── pyproject.toml   uv workspace root (members: app, libs/*); uv.lock pins every dependency
├── Dockerfile       the single backend image (targets: test, runtime)
├── app/             zoiko-id-platform: the modular monolith (all 29 modules)
├── libs/            zid-* platform libraries (mechanics only, bundled into the same image)
├── database/        schema bootstrap, table-ownership registry, roles, extensions, RLS helpers — no tables
├── config/          versioned platform configuration: jurisdiction packs, crypto profiles, residency profiles, cells
└── policies/        Cedar schema, platform-floor policies, policy tests
```

### 2.1 Modules and planes

One package, one image. Modules are grouped into planes (`app/src/zid_platform/roles.py`). The `cell` role serves every plane except Global; the `global` role serves only Global.

| Plane | Modules (logical services) | Tables live in schemas |
|---|---|---|
| Runtime | principal (PRN), authentication (AUTHN), session (SES), decision (AUTHZ), policy_runtime (POL runtime), relationship (REL), authority (AUTHY), mandate (MAND), machine (MACH) | `core`, `authn`, `authz`, `org`, `agent` |
| Trust | organization (ROOT), verification (VER), credential (CRED) | `org`, `verify`, `cred` |
| Security / Evidence | signals (SIG), evidence (EVID), privacy (PRIV) | `signals`, `evidence`, `privacy` |
| Integration | federation (FED), provisioning (SCIM), integration (INT), onboarding (ONB), webhook (WHK) | `authn`, `platform`, `events` |
| Control / Support | tenant (TEN), configuration (CFG), policy_authoring (POL), operator_access (OPS), metering (METER), notification (NOTIF), search (SEARCH) | `platform`, `authz`, `metering` |
| Global | locator, discovery | `global_directory` (separate global database; no personal data) |
| Event Platform | outbox stream (`entrypoints/outbox_relay.py`) | `events` (outbox, inbox) |

### 2.1.1 Ownership is per table, not per schema
The specs disagree on granularity:
- **Data Model** (ZID-DATA-001 §1.1) groups schemas by broad boundary. For example, `authz` holds grants, policies and decisions.
- **Service spec** (ZID-SVC-001 §5–6) gives those tables to different services (AUTHY, POL, AUTHZ), each owning "its tables/schema and migration lifecycle".

We keep the Data Model's schema **names** as namespaces, and assign ownership **per table**:

- **[`backend/database/table-ownership.yaml`](../../backend/database/table-ownership.yaml)** is the single source of truth: 59 tables, exactly one owning module each.
- Each module that owns tables has **its own Alembic stream**, in `migrations/<module>/`, with its own version table in the `zid_migrations` schema. There are 27 streams, all in `backend/app/migrations/`.
- CI rejects a migration that creates or alters a table its module doesn't own. Per-service DB roles get **table-level** grants generated from the registry.
- Schemas themselves are created once, by `backend/database/schemas/` (no module owns a whole shared schema).
- Tables marked `proposed` in the registry aren't named in Data Model v1.0. Each needs a Data Model change request before its first migration (see the [spec register](../specs/README.md)).

### 2.2 Inside the monolith

```
backend/app/
├── pyproject.toml               zoiko-id-platform; depends on zid-* libs + uvicorn
├── migrations/                  ONE Alembic stream per owning module (27 streams)
│   ├── mandate/                 agent.mandate, agent.mandate_balance, agent.mandate_reservation, agent.writer_fence
│   ├── authority/               authz.authority_grant, authz.resource_scope, authz.data_scope
│   └── …
├── src/zid_platform/
│   ├── main.py                  app factory: composes the role's modules, middleware, lifespans
│   ├── roles.py                 cell | global → modules + schemas; plane grouping
│   ├── settings.py              typed settings (ZID_*), no secret defaults
│   ├── entrypoints/
│   │   ├── supervisor.py        container default CMD: runs ZID_ROLES (cell :8000, global :8100) as separate processes
│   │   ├── api.py               FastAPI (uvicorn) app for one role
│   │   ├── consumer.py          Kafka consumers (inbox-deduplicated), e.g. relationship → SpiceDB projector
│   │   ├── worker.py            Temporal workers, e.g. onboarding sagas, evidence checkpointer
│   │   └── outbox_relay.py      outbox → Kafka relay (Event Platform)
│   └── modules/                 29 modules
│       └── mandate/
│           ├── ports.py         public Protocol: the ONLY thing other modules may import
│           ├── api/             routers + Pydantic request/response models
│           ├── application/     use cases, transaction boundaries, outbox writes
│           ├── domain/          pure Python: aggregates, invariants, state machines, reason codes
│           └── infrastructure/  SQLAlchemy models/repos, clients, adapters
└── tests/
    ├── unit/                    domain logic + architecture guards (test_architecture.py)
    ├── property/                Hypothesis (authority algebra, ledger invariants)
    ├── component/               real Postgres/Kafka via testcontainers
    └── contract/                OpenAPI/AsyncAPI producer-consumer checks
```

**Layer dependency rule:**

```
api ──► application ──► domain ◄── infrastructure
                 └────────────────────►┘ (via domain-defined repository Protocols)
```

- `domain/` imports nothing from FastAPI, SQLAlchemy, httpx, Kafka or any other module.
- A module may import another module's `ports.py` only, never its `domain/`, `application/`, `infrastructure/` or ORM models. **`tests/unit/test_architecture.py` fails the build on any violation**, which is what keeps this a *modular* monolith.
- One image; the process type is chosen by entrypoint, and the module set by `ZID_ROLE`.

**Extracting a module later:** move `modules/<name>/` and `migrations/<name>/` into a new service, and swap its `ports.py` adapter for an HTTP client generated from `contracts/openapi/internal/`. Callers don't change. The only pre-approved extraction is `decision` (ADR-0021).

### 2.3 `backend/libs/`: shared platform libraries

| Library | Purpose |
|---|---|
| `zid-app` | FastAPI service shell: settings, request-context middleware, canonical error envelopes, request logging, `/health/live`, `/health/ready`, `/` |
| `zid-context` | RequestContext (tenant, cell, epoch, principal, actor chain, deadline, risk class) |
| `zid-db` | Session factory with mandatory `SET LOCAL` tenant/principal; RLS guard |
| `zid-outbox` | Transactional outbox writer + consumer inbox dedup |
| `zid-idempotency` | Idempotency-Key store, fingerprinting, result replay |
| `zid-errors` | Canonical error codes and envelopes |
| `zid-authz-client` | Internal PEP client (AuthZEN, obligations, commit re-check) |
| `zid-evidence` | Typed E1–E3 evidence envelopes, JCS canonicalization |
| `zid-crypto` | Crypto broker over KMS/HSM, envelope encryption, algorithm allowlists |
| `zid-telemetry` | OpenTelemetry + PII-safe logging |
| `zid-testing` | testcontainers fixtures, golden fixtures, isolation-test helpers |

**Rule:** libraries hold platform mechanics only. **No domain entities, no ORM models and no business rules.** A shared domain model is the coupling the Service spec forbids.

### 2.4 `backend/database/`, `backend/config/` and `backend/policies/`

- `database/`:
  - `schemas/`: creates the namespaces. `001_cell_schemas.sql` creates the 13 Data Model schemas plus `zid_migrations`; `001_global_schemas.sql` does the same for the global database.
  - `table-ownership.yaml`: the ownership registry (§2.1.1).
  - `roles/`: per-service runtime roles, `db_owner_migration`, `readonly_auditor`, JIT break-glass.
  - `extensions/` and `rls/`: shared RLS helper functions.
  - **No tables live here.** Tables belong to the owning module's `migrations/<module>/`.
- `config/`: versioned, reviewed platform configuration that services load at runtime.
  - `jurisdiction-packs/` (ADR-0007; Legal approves)
  - `crypto-profiles/` (the Crypto Policy Registry; ADR-0009, ADR-0026)
  - `residency-profiles/` (SRP-0 to SRP-3)
  - `cells/` (cell catalog and recovery pairs; ADR-0011)

  This is **not** tenant data. Releases are immutable.
- `policies/`: `cedar/schema/` (base entity and action types; tenant schemas are generated from it), `cedar/platform-floor/` (non-overridable fail-closed and deny rules) and `tests/` (golden allow/deny cases run against every policy release). Tenant-authored policies are **data**, stored and released by the `policy_authoring` module, not files in this repo.

---

## 3. `frontend/`

```
frontend/
├── package.json · pnpm-workspace.yaml · turbo.json
├── apps/
│   ├── tenant-console/      console.id.zoiko.com
│   ├── developer-console/   developers.id.zoiko.com
│   ├── trust-center/        my.id.zoiko.com
│   ├── hosted-login/        id.zoiko.com (UI only; OIDC/OAuth/SAML endpoints stay in the backend `session`/`authentication` modules)
│   └── operator-console/    separate origin, client, identity policy and deploy path
└── packages/
    ├── ui/                  React Aria + Tailwind design system
    │   └── src/{components,tokens,patterns/sensitive-action}
    ├── api-client/          generated from ../contracts/openapi
    │   └── src/{generated,hooks}
    ├── auth-bff/            thin BFF helpers
    │   └── src/{session,proxy,csp}
    └── config/              shared eslint, tsconfig, tailwind presets
```

### 3.1 Inside one app

All five apps use the same layout. `tenant-console` is shown here:

```
frontend/apps/tenant-console/
├── src/
│   ├── app/                         Next.js App Router
│   │   ├── (console)/               route group sharing the console shell
│   │   │   ├── layout.tsx           uses components/shell (tenant switcher, nav, alerts, search)
│   │   │   ├── overview/  people/  organizations/  verification/  credentials/
│   │   │   ├── access-policies/  decision-simulator/  delegated-authority/  agents/
│   │   │   └── sessions-signals/  integrations/  evidence-audit/  privacy/  settings/  support/
│   │   ├── api/bff/[...path]/       BFF proxy: attach session token, forward to cell API
│   │   └── auth/{login,callback,logout}/   OIDC login to Zoiko for console users
│   ├── features/<feature>/          domain UI code, one folder per feature
│   │   ├── components/              feature components
│   │   ├── hooks/                   TanStack Query hooks over the generated client
│   │   └── schemas/                 Zod form schemas
│   ├── components/                  app-specific shared components
│   │   └── shell/                   console shell: tenant switcher, nav, alerts, search
│   ├── lib/                         app utilities
│   └── i18n/messages/               next-intl message catalogs
├── public/
└── tests/{unit,e2e}/
```

**Routes (`app/`) stay thin.** They compose feature components. All UI logic lives in `features/`.

### 3.2 Route map per app (from the UI/UX Wireframes spec)

| App | Route group | Routes |
|---|---|---|
| tenant-console | `(console)` | overview, people, organizations, verification, credentials, access-policies, decision-simulator, delegated-authority, agents, sessions-signals, integrations, evidence-audit, privacy, settings, support |
| developer-console | `(console)` | applications, api-access, webhooks, events, sandbox, sdks, logs, rate-limits |
| trust-center | `(account)` | sign-in-methods, sessions, delegations, consents, credentials, privacy-requests |
| hosted-login | `(flows)` | login, federate, passkey, step-up, consent, approval, recovery, error |
| operator-console | `(operator)` | tenant-health, cell-health, incidents, support-cases, migrations, evidence-integrity, jit-access |

`hosted-login` has `api/flow/[...path]` instead of a BFF, because it drives authentication flows in the backend and has no console session yet.

### 3.3 Frontend rules (ADR-0013)

- The frontend never makes authorization decisions, holds business logic or touches a database. The backend re-authorizes every mutation. Hiding a button is cosmetic only.
- The browser holds only an httpOnly session cookie. Tokens stay server-side in the BFF.
- Sensitive actions use the shared `ui/patterns/sensitive-action` flow: intent, impact, preconditions, step-up/approval, then commit.
- `operator-console` must not share session configuration with customer apps.
- Accessibility (WCAG 2.2 AA) is checked with axe in CI.

---

## 4. Root shared folders

### 4.1 `contracts/`

```
contracts/
├── openapi/{public,tenant-admin,developer,operator,authzen,internal}/
├── asyncapi/{channels,schemas}/     AsyncAPI 3.1 + event payload schemas
├── error-codes/                     canonical error/reason code registry
├── action-catalog/                  protected actions: risk class, evidence class, required facts
└── interface-registry/              one entry per route/RPC/event: owner, SLO, consistency, data and evidence class (ZID-SVC-001 §22)
```

- Backend internal clients, `frontend/packages/api-client` and `sdks/*` are **generated** from here by `tools/codegen/`.
- CI runs breaking-change checks against `main` on every pull request.
- The **action catalog** is the single place where each action's minimum risk class (R0–R3) and evidence class (E0–E3) is declared. Callers can raise these but never lower them.

### 4.2 `sdks/`

`python/` and `typescript/` come in W1; `java/`, `go/` and `dotnet/` in later waves; `envoy-ext-authz/` is the gateway enforcement plugin. Each SDK has its own build tooling, because customers install them separately. They contain generated clients plus hand-written obligation handling, discovery caching and `CELL_ROUTING_STALE` re-discovery.

### 4.3 `deploy/`

```
deploy/
├── tofu/
│   ├── modules/{env-baseline,global-plane,cell,eks,aurora,msk,kms-hsm,observability}/
│   └── live/{dev,int-qa,sec,perf,preprod,prod,recovery}/   one folder per environment; cells inside
├── helm/charts/             one chart per backend unit + one per frontend app
├── argocd/cells/            app-of-apps per cell
└── policies/kyverno/        admission: signed images, provenance, no privileged pods
```

### 4.4 `tests/`: system-level suites

| Folder | Proves | Gate |
|---|---|---|
| `golden/` | Shared fixtures: authority chains, mandates, revisions, credential status, erasure, replay | used by all |
| `e2e/` | First-customer journey across backend and frontend (Playwright) | G2 |
| `concurrency/` | Mandate 10k race, crash injection, reconciliation | P0-03 |
| `tenant-isolation/` | Cross-tenant and cross-cell escape attempts | G2 blocker |
| `load/` | k6 at Stage A and 2× | ADR-0021, G5 |
| `chaos/` | AZ/dependency/region failure, R0–R3 revocation SLOs | W2, G3 |
| `conformance/` | OpenID, AuthZEN, SSF, OID4VC suites | G6 |
| `security/` | Abuse cases, fuzzing, DAST | G4 |

Unit, property, component and contract tests live **inside** each backend service or library. Unit and e2e tests for one app live inside that frontend app.

### 4.5 `docs/`

| Folder | Contents |
|---|---|
| `specs/` | The 30 canonical documents (upstream authority) + [README.md](../specs/README.md) register: IDs, versions, status, duplicates, open spec change requests |
| `architecture/` | ZID-SAD-001, this document |
| `adr/` | Architecture decision records |
| `traceability/` | Master matrix: requirement → object → interface → threat → test |
| `threat-model/` | STRIDE/LINDDUN models per trust boundary |
| `runbooks/` | Operational runbooks (revocation, failover, key compromise, restore) |

---

## 5. Naming conventions

| Thing | Convention | Example |
|---|---|---|
| Backend app package | `zid_platform` (distribution `zoiko-id-platform`) | `zid_platform.modules.mandate` |
| Backend module | snake_case singular | `mandate`, `policy_runtime` |
| Library distribution | `zid-` + kebab-case | `zid-outbox` |
| Frontend app / package | kebab-case | `tenant-console`, `api-client` |
| Frontend route segment | kebab-case | `delegated-authority` |
| Frontend feature folder | kebab-case | `authority-grants` |
| Postgres schema | short lowercase | `agent`, `authz` |
| Event type | `com.zoiko.id.<domain>.<noun>.<past-tense>.v<major>` | `com.zoiko.id.mandate.reservation.committed.v1` |
| Kafka topic | `zid.<env>.<cell>.<domain>.<class>` | `zid.prod.uk-1.signals.priority` |
| OpenAPI path | plural nouns, kebab-case | `/v1/authority-grants` |
| JSON fields | snake_case | `policy_release_id` |

---

## 6. CI enforcement (added in W0)

- **Architecture tests** (`backend/app/tests/unit/test_architecture.py`): "ports only" rule between modules, plane map, migration and table-owner consistency (ADR-0031).
- **Table ownership check:** a migration may create or alter only tables its module owns in `backend/database/table-ownership.yaml`; every tenant-scoped table must have an RLS policy.
- **Interface registry check:** every route, RPC and event has an entry in `contracts/interface-registry/`; declared dependencies must respect the acyclic rules.
- **Frontend boundary lint:** no database clients, no Kafka, and no authorization logic in `frontend/`. ESLint import rules keep `app/` routes thin.
- **Contract diff:** OpenAPI/AsyncAPI breaking-change detection.
- **CODEOWNERS:** trust-kernel paths need Security review.
- **No secrets:** gitleaks.
- **Dependency audit:** pip-audit and pnpm audit.
