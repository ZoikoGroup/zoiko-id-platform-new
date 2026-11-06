# Migrations: `keys` stream

Alembic stream for the cell's software key store (CR-057, platform stream like `outbox`). Version table: `zid_migrations.alembic_version__keys`.

May create or alter **only** these tables (enforced by CI against `backend/database/table-ownership.yaml`):

- `keys.key_version`
- `keys.key_event`
