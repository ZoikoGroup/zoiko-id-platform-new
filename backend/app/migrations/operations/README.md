# Migrations: `operations` module

Alembic stream owned by the **operations** module (zoiko-id-platform, DEC-D6). Version table: `zid_migrations.alembic_version__operations`.

May create or alter **only** these tables (enforced by CI against `backend/database/table-ownership.yaml`):

- `platform.incident`
- `platform.incident_entry`
- `platform.incident_action_item`
- `platform.readiness_review`
- `platform.readiness_gate`
- `platform.change_freeze`
