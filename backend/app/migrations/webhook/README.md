# Migrations: `webhook` module

Alembic stream owned by the **webhook** module (zoiko-id-platform, DEC-D6). Version table: `zid_migrations.alembic_version__webhook`.

May create or alter **only** these tables (enforced by CI against `backend/database/table-ownership.yaml`):

- `events.webhook_endpoint`
- `events.webhook_delivery`
