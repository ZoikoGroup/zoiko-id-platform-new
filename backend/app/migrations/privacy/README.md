# Migrations: `privacy` module

Alembic stream owned by the **privacy** module (zoiko-id-platform, ADR-0031). Version table: `zid_migrations.alembic_version__privacy`.

May create or alter **only** these tables (enforced by CI against `backend/database/table-ownership.yaml`):

- `privacy.consent_receipt`
- `privacy.purpose`
- `privacy.processing_role_assignment`
