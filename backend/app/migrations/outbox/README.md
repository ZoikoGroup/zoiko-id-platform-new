# Migrations: `outbox` module

Alembic stream owned by the **outbox** module (zoiko-id-platform, ADR-0031). Version table: `zid_migrations.alembic_version__outbox`.

May create or alter **only** these tables (enforced by CI against `backend/database/table-ownership.yaml`):

- `events.outbox_event`
- `events.inbox_message`
