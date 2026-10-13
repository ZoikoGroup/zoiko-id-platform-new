# Migrations: `federation` module

Alembic stream owned by the **federation** module (zoiko-id-platform, DEC-D6). Version table: `zid_migrations.alembic_version__federation`.

May create or alter **only** these tables (enforced by CI against `backend/database/table-ownership.yaml`):

- `authn.federation_connection`
- `authn.federation_login`
- `authn.saml_assertion_replay`
- `authn.federation_trust_anchor`
