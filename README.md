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
deploy/      OpenTofu, Helm, Argo CD, Kyverno, single-VM dev deployment
tests/       system-level suites (e2e, concurrency, tenant isolation, load, chaos, conformance)
local/       local dev stack config + synthetic seed data
tools/       code generation and scripts
docs/        specs, architecture, ADRs, traceability, threat model, runbooks
```

PostgreSQL **always runs in Docker** (`docker compose`); no local PostgreSQL install is needed. The backend can run in Docker too (option A) or directly on your machine (option B).

## Option A: everything in Docker

Requires Docker Desktop.

```bash
docker compose up --build -d             # Postgres + ONE backend container (cell :8000 + global :8100)
curl http://localhost:8000/health/ready  # cell role: all 27 modules, DB + owned schemas checked
curl http://localhost:8100/health/ready  # global role: locator + discovery, global database
docker compose logs -f backend           # structured JSON logs from both roles
docker compose down                      # stop (add -v to wipe the database)
docker build --target test backend       # run the backend test suite
```

Interactive API docs: http://localhost:8000/docs (cell) and http://localhost:8100/docs (global). Endpoints: `/`, `/health/live`, `/health/ready`.

## Option B: backend on your machine, PostgreSQL in Docker (Windows PowerShell)

Requires Docker Desktop, plus Python (any version) only to install uv.

```powershell
# 1. One-time: install uv
python -m pip install --user uv

# 2. Start only the database container (Postgres 17 on 127.0.0.1:5432; schemas created automatically)
cd C:\Users\HP\Downloads\zoiko_ids
docker compose up -d postgres

# 3. Every new terminal: point uv at a venv OUTSIDE Downloads
#    (Windows Application Control on some machines blocks python.exe under Downloads)
cd C:\Users\HP\Downloads\zoiko_ids\backend
$env:UV_PROJECT_ENVIRONMENT = "$env:LOCALAPPDATA\zoiko-id\venv"
python -m uv sync --all-packages          # install exactly what uv.lock says

# 4. Start the backend: ONE terminal, both roles (Ctrl+C stops both)
#    cell   (27 modules)            -> http://localhost:8000
#    global (locator + discovery)   -> http://localhost:8100
python -m uv run python ..\tools\scripts\run_local.py
#    only the cell role (enough for most work):
python -m uv run python ..\tools\scripts\run_local.py --only cell

# Tests
python -m uv run pytest -q libs app
```

Check: http://localhost:8000/health/ready should return `"status": "ready"`. Stop the database with `docker compose stop postgres` (`docker compose down -v` also deletes its data).

Don't run the `backend` container (option A) and the launcher (option B) at the same time; both use ports 8000 and 8100.

The two roles stay **separate processes** (the global plane holds no personal data and uses its own database, ZID-TENANCY-001 §2). The launcher runs both from one terminal, labels their output `[cell]` / `[global]`, and reloads automatically on code changes. Database URLs can be overridden with `ZID_CELL_DATABASE_URL` / `ZID_GLOBAL_DATABASE_URL`.

## Stack

Python 3.13+ / FastAPI · Next.js + TypeScript · PostgreSQL (Aurora) · SpiceDB · Cedar · Kafka (MSK) · Valkey · Temporal · SPIFFE/SPIRE · KMS/CloudHSM · EKS · OpenTofu · Argo CD · OpenTelemetry

## Status

Architecture baseline: **Draft**. Build wave: **W0 (architecture proof)**, with the modular-monolith service shell running (cell + global roles).
