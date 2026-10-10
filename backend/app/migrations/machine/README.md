# Migrations: `machine` module

Alembic stream owned by the **machine** module (zoiko-id-platform, ADR-0031). Version table: `zid_migrations.alembic_version__machine`.

May create or alter **only** these tables (enforced by CI against `backend/database/table-ownership.yaml`):

- `agent.agent_profile`
- `agent.workload_binding`
