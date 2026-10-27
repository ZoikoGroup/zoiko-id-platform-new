# Migrations: `tenant` module

Alembic stream owned by the **tenant** module (zoiko-id-platform, DEC-D6). Version table: `zid_migrations.alembic_version__tenant`.

May create or alter **only** these tables (enforced by CI against `backend/database/table-ownership.yaml`):

- `platform.tenant`
- `platform.cell`
- `platform.tenant_shard`
- `platform.entitlement`
- `platform.tenant_residency`
- `platform.residency_transfer_rule`
- `platform.tenant_role_assignment`
- `platform.tenant_state_transition`
- `platform.tenant_configuration_revision`
- `platform.tenant_bootstrap_request`
- `platform.tenant_domain`
- `platform.workspace_directory`
- `platform.directory_publication`
- `platform.tenant_registration`
