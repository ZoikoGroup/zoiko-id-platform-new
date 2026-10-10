# Migrations: `policy_authoring` module

Alembic stream owned by the **policy_authoring** module (zoiko-id-platform, ADR-0031). Version table: `zid_migrations.alembic_version__policy_authoring`.

May create or alter **only** these tables (enforced by CI against `backend/database/table-ownership.yaml`):

- `authz.policy_definition`
- `authz.policy_release`
