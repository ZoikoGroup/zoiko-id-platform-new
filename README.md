# Zoiko iD

Overlay-first trust infrastructure: identity, verification, credentials, organizational authority, delegated mandates, AI-agent identity, continuous revocation and defensible decision evidence.

## Start here

| Read | Why |
|---|---|
| [docs/specs/](docs/specs/) | The canonical specifications (upstream authority) |
| [docs/architecture/zoiko-id-architecture.md](docs/architecture/zoiko-id-architecture.md) | Buildable architecture (ZID-SAD-001) |
| [docs/architecture/repository-structure.md](docs/architecture/repository-structure.md) | What lives where, and the rules |
| [docs/adr/](docs/adr/README.md) | Architecture decisions |

## Layout

```
backend/     Python / FastAPI modular monolith: app (29 modules), libs, database bootstrap, config, Cedar policies
frontend/    Next.js: tenant/developer consoles, trust center, hosted login, operator console
contracts/   OpenAPI, AsyncAPI, error codes, action catalog (shared by backend, frontend, SDKs)
sdks/        PEP SDKs for customer apps
deploy/      OpenTofu, Helm, Argo CD, Kyverno
tests/       system-level suites (e2e, concurrency, tenant isolation, load, chaos, conformance)
local/       local dev stack config + synthetic seed data
tools/       code generation and scripts
docs/        specs, architecture, ADRs, traceability, threat model, runbooks
```

## Run locally

Requires Docker Desktop (no local Python needed).

```bash
docker compose up --build -d             # Postgres + the backend (one image, two roles)
curl http://localhost:8000/health/ready  # cell role: all 27 modules, DB + owned schemas checked
curl http://localhost:8100/health/ready  # global role: locator + discovery, global database
docker compose logs -f zid-cell          # structured JSON logs
docker compose down                      # stop (add -v to wipe the local database)
docker build --target test backend       # run the backend test suite
```

Interactive API docs: http://localhost:8000/docs (cell) and http://localhost:8100/docs (global). Endpoints: `/`, `/health/live`, `/health/ready`.

## Stack

Python 3.13+ / FastAPI · Next.js + TypeScript · PostgreSQL (Aurora) · SpiceDB · Cedar · Kafka (MSK) · Valkey · Temporal · SPIFFE/SPIRE · KMS/CloudHSM · EKS · OpenTofu · Argo CD · OpenTelemetry

## Status

Architecture baseline: **Draft**. Build wave: **W0 (architecture proof)** — modular-monolith service shell running (cell + global roles).
