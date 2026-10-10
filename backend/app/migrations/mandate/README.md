# Migrations: `mandate` module

Alembic stream owned by the **mandate** module (zoiko-id-platform, ADR-0031). Version table: `zid_migrations.alembic_version__mandate`.

May create or alter **only** these tables (enforced by CI against `backend/database/table-ownership.yaml`):

- `agent.mandate`
- `agent.mandate_balance`
- `agent.mandate_reservation`
- `agent.writer_fence`
