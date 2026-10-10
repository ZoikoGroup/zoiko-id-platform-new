# Migrations: `operator_access` module

Alembic stream owned by the **operator_access** module (zoiko-id-platform, ADR-0031). Version table: `zid_migrations.alembic_version__operator_access`.

May create or alter **only** these tables (enforced by CI against `backend/database/table-ownership.yaml`):

- `platform.jit_grant`
- `platform.lockbox_request`
