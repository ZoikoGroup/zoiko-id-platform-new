# Backend

All Zoiko iD server-side code: Python 3.13+ / FastAPI, **modular monolith** (ADR-0031), one uv workspace.

```
backend/
├── pyproject.toml   uv workspace root (members: app, libs/*)
├── uv.lock          exact versions + hashes for every dependency
├── Dockerfile       the single backend image (targets: test, runtime)
├── app/             zoiko-id-platform — 29 modules, roles (cell | global), entrypoints, migrations
├── libs/            zid-* platform libraries (zid-app = shared FastAPI service shell; mechanics only)
├── database/        schema bootstrap, table-ownership.yaml, roles, extensions, RLS helpers (no tables)
├── config/          jurisdiction packs, crypto profiles, residency profiles, cell catalog
└── policies/        Cedar schema, platform-floor policies, policy tests
```

One image runs in two roles: `ZID_ROLE=cell` (27 cell modules, deployed per regional cell) and `ZID_ROLE=global` (locator + discovery on the global database). Background work runs as separate processes of the same image: `python -m zid_platform.entrypoints.<consumer|worker|outbox_relay>`.

Every table has exactly one owning module ([database/table-ownership.yaml](database/table-ownership.yaml)), with its own Alembic stream in `app/migrations/<module>/`. Modules talk only through `ports.py`; `app/tests/unit/test_architecture.py` enforces it.

```bash
docker build --target test .     # run all backend tests
```

Full rules: [docs/architecture/repository-structure.md](../docs/architecture/repository-structure.md).
