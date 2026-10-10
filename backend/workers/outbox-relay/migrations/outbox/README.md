# Migrations: `outbox` module

Alembic stream owned by **outbox-relay/outbox**. Version table: `zid_migrations.alembic_version__outbox_relay__outbox`.

May create or alter **only** these tables (enforced by CI against `backend/database/table-ownership.yaml`):

- `events.outbox_event`
- `events.inbox_message`
