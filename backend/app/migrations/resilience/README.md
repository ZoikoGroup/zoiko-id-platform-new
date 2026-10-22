# Migrations: `resilience` module

Alembic stream owned by the **resilience** module (zoiko-id-platform, DEC-D6). Version table: `zid_migrations.alembic_version__resilience`.

May create or alter **only** these tables (enforced by CI against `backend/database/table-ownership.yaml`):

- `platform.cell_state`
- `platform.cell_readiness`
- `platform.recovery_operation`
- `platform.recovery_step`
- `platform.dr_exercise`
