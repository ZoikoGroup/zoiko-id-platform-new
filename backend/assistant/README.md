# Public assistant service

Anonymous, **information-only** assistant for a public website. It explains Zoiko iD concepts from approved public content, cites its
sources, and points visitors to official pages. It cannot sign anyone in, verify anyone, read a record, reset anything or touch a
tenant, and it is built so that it **has no way to** (ZI-CHAT-ADR-006 INV-01).

It is a **separate service** from the platform: its own process, image, database, role and port. It shares only the platform's service
shell libraries (`zid-app`, `zid-errors`, `zid-telemetry`) and never imports `zid_platform`.

> **Everything here is a non-public prototype.** The launch content is an engineering sample, every feature flag defaults OFF, and no
> gate (G0..G4) is signed. See [docs/phases/phase-m-public-assistant.md](../../docs/phases/phase-m-public-assistant.md) and
> [the evidence ledger](../../docs/AI_Assistance/EVIDENCE_LEDGER.md).

## Run it

```powershell
docker compose up -d postgres                      # ZID_DB_HOST_PORT=5440 if 5432 is taken
cd backend
Copy-Item assistant\.env.example assistant\.env    # local demo values (gitignored); without it every flag is OFF
python -m uv sync --all-packages
python -m zid_assistant.migrate                    # creates the zid_assistant database, schema and least-privilege role
python -m zid_assistant                            # http://localhost:8210  (GET /health/ready)
```

The browser never calls it directly. The platform web app proxies it (`/api/public/chat/v1/*`) and serves a preview page:

```powershell
cd ..\frontend; npm run dev:web                    # http://localhost:3000/assistant-preview
```

Or in Docker: `docker compose --profile assistant up -d --build assistant-valkey assistant-migrate assistant`.

## Layout

```
src/zid_assistant/
  settings.py            ZID_ASSISTANT_* (no identity settings; flags all OFF)
  domain/                pure rules: content registers P01..P05, eligibility, secret interceptor, answer packet, intake
  application/           snapshot loader, router, retrieval, verifier, pipeline, intake service, retention
  infrastructure/        sessions (Valkey/memory), rate limiter, flags + kill switch, revocations, stores, delivery, model gateway
  api/                   routes, errors, state          main.py (app factory)   migrate.py (SQL runner)
content/                 the publication registers and the 28 launch answers as YAML (a SAMPLE snapshot)
sql/                     migrations for the public_assistant schema
contract/                assistant-v1.openapi.json, generated from the code
API.md                   the human-readable contract
tests/                   unit, security (isolation + P0), golden corpus, component (Postgres, Valkey)
```

## Operate it

| Task | Command |
|---|---|
| Emergency stop (no deploy) | `python -m zid_assistant.flags_cli set kill_switch on` (needs `ZID_ASSISTANT_VALKEY_URL`) |
| Stop only the model | `... set ai_answers off` |
| Withdraw a source or link instantly | `... revoke source PUB-005` / `revoke link OVERVIEW` |
| Validate / digest / sign the content | `python -m zid_assistant.content_tool validate \| digest \| keygen \| sign \| verify` |
| Re-generate the API contract | `python -m zid_assistant.contract_tool export` (CI fails if it drifts) |
| Full procedure | [docs/runbooks/public-assistant.md](../../docs/runbooks/public-assistant.md) |

## Test it

```powershell
python -m pytest assistant/tests -q        # 341 tests; the component tests need Postgres and Valkey and skip without them
ruff check assistant ; ruff format --check assistant ; mypy assistant/src
```

## Turning features on (not before the decisions are signed)

| Feature | Setting | Blocked on |
|---|---|---|
| Chat | `ZID_ASSISTANT_ENABLED=true` | Gates G0..G4 for production |
| Model answers | `ZID_ASSISTANT_AI_ANSWERS=true`, `MODEL_PROVIDER=claude`, `MODEL_API_KEY_FILE=<file>` | OD-010/016/017 (vendor, terms, budget) |
| Sales / support forms | `INTAKE_SALES` / `INTAKE_SUPPORT` + `INTAKE_COUNTRIES` + `INTAKE_NOTICE_*_ID` + a receiver | OD-009..015 (legal basis, notices, receivers) |
| Production content | a **signed, non-sample** snapshot | OD-004..007 (P01..P05 owners) |

A production process refuses to start with a sample or unsigned snapshot, and without an intake encryption key file.
