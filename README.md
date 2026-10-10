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
backend/     Python / FastAPI: services, workers, libs, database bootstrap, Cedar policies
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
docker compose up --build -d            # Postgres + all 8 backend units
curl http://localhost:8001/health/ready # readiness: DB reachable + owned schemas present
docker compose logs -f identity-runtime # structured JSON logs
docker compose down                     # stop (add -v to wipe the local database)
docker build --target test backend      # run the backend test suite
```

| Port | Unit | Port | Unit |
|---|---|---|---|
| 8001 | identity-runtime | 8005 | security-evidence |
| 8002 | authorization-runtime | 8006 | enterprise-integration |
| 8003 | authority-runtime | 8007 | platform-control |
| 8004 | trust-services | 8008 | global-locator |

Each unit serves `/` (identity), `/health/live`, `/health/ready` and interactive API docs at `/docs`.

## Stack

Python 3.13+ / FastAPI · Next.js + TypeScript · PostgreSQL (Aurora) · SpiceDB · Cedar · Kafka (MSK) · Valkey · Temporal · SPIFFE/SPIRE · KMS/CloudHSM · EKS · OpenTofu · Argo CD · OpenTelemetry

## Status

Architecture baseline: **Draft**. Build wave: **W0 (architecture proof)** — service shell running for all 8 units.
