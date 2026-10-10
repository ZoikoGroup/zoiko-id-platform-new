# Migrations: `authentication` module

Alembic stream owned by the **authentication** module (zoiko-id-platform, ADR-0031). Version table: `zid_migrations.alembic_version__authentication`.

May create or alter **only** these tables (enforced by CI against `backend/database/table-ownership.yaml`):

- `authn.authenticator`
- `authn.external_identity_link`
