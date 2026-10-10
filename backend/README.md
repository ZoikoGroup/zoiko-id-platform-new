# Backend

All Zoiko iD server-side code: Python 3.13+ / FastAPI, one uv workspace.

```
backend/
├── pyproject.toml   uv workspace root (members: services/*, workers/*, libs/*)
├── services/        8 deployable units (7 cell units + global-locator)
├── workers/         outbox-relay (the only domain-neutral worker)
├── libs/            zid-* platform libraries (mechanics only, no domain models)
├── database/        schema bootstrap, table-ownership.yaml, roles, extensions, RLS helpers (no tables)
├── config/          jurisdiction packs, crypto profiles, residency profiles, cell catalog
└── policies/        Cedar schema, platform-floor policies, policy tests
```

Every table has exactly one owning module ([database/table-ownership.yaml](database/table-ownership.yaml)). Each owning module has its own Alembic stream in `services/<unit>/migrations/<module>/`. Modules inside a unit talk only through `ports.py`.

Full rules: [docs/architecture/repository-structure.md](../docs/architecture/repository-structure.md).
