# Migrations: `metering` module

Alembic stream owned by the **metering** module (zoiko-id-platform, ADR-0031). Version table: `zid_migrations.alembic_version__metering`.

May create or alter **only** these tables (enforced by CI against `backend/database/table-ownership.yaml`):

- `metering.usage_event`
- `metering.correction`
