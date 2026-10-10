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

## Run manually (without Docker, Windows PowerShell)

Requires Python (any version, only to install uv) and PostgreSQL 17+ on `localhost:5432`. Don't run the Docker stack at the same time; it uses the same port.

```powershell
# 1. One-time: install uv
python -m pip install --user uv

# 2. One-time: database role, databases and schemas (asks for your postgres password)
$psql = "C:\Program Files\PostgreSQL\18\bin\psql.exe"
& $psql -h localhost -U postgres -c "CREATE ROLE zid LOGIN PASSWORD 'zid_local_only';"
& $psql -h localhost -U postgres -c "CREATE DATABASE zid_cell OWNER zid;"
& $psql -h localhost -U postgres -c "CREATE DATABASE zid_global OWNER zid;"
& $psql -h localhost -U zid -d zid_cell   -f backend\database\schemas\001_cell_schemas.sql
& $psql -h localhost -U zid -d zid_global -f backend\database\schemas\001_global_schemas.sql

# 3. Every new terminal: point uv at a venv OUTSIDE Downloads
#    (Windows Application Control on some machines blocks python.exe under Downloads)
cd C:\Users\HP\Downloads\zoiko_ids\backend
$env:UV_PROJECT_ENVIRONMENT = "$env:LOCALAPPDATA\zoiko-id\venv"
python -m uv sync --all-packages          # install exactly what uv.lock says

# 4a. Terminal 1 — cell role (27 modules) on http://localhost:8000
$env:ZID_ROLE = "cell"; $env:ZID_CELL_ID = "local-1"
$env:ZID_DATABASE_URL = "postgresql+asyncpg://zid:zid_local_only@localhost:5432/zid_cell"
python -m uv run uvicorn zid_platform.entrypoints.api:app --port 8000 --reload

# 4b. Terminal 2 — global role (locator + discovery) on http://localhost:8100
$env:ZID_ROLE = "global"; $env:ZID_CELL_ID = "global"
$env:ZID_DATABASE_URL = "postgresql+asyncpg://zid:zid_local_only@localhost:5432/zid_global"
python -m uv run uvicorn zid_platform.entrypoints.api:app --port 8100 --reload

# Tests
python -m uv run pytest -q libs app
```

Check: http://localhost:8000/health/ready should return `"status": "ready"`. Stop a server with `Ctrl+C`.

## Stack

Python 3.13+ / FastAPI · Next.js + TypeScript · PostgreSQL (Aurora) · SpiceDB · Cedar · Kafka (MSK) · Valkey · Temporal · SPIFFE/SPIRE · KMS/CloudHSM · EKS · OpenTofu · Argo CD · OpenTelemetry

## Status

Architecture baseline: **Draft**. Build wave: **W0 (architecture proof)** — modular-monolith service shell running (cell + global roles).
