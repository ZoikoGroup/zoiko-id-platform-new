# Migrations: `tenant` module

Alembic stream owned by the **tenant** module (zoiko-id-platform, DEC-D6). Version table: `zid_migrations.alembic_version__tenant`.

May create or alter **only** these tables (enforced by CI against `backend/database/table-ownership.yaml`):

- `platform.tenant`
- `platform.cell`
- `platform.tenant_shard`
- `platform.entitlement`
