# Migrations: `session` module

Alembic stream owned by the **session** module (zoiko-id-platform, DEC-D6). Version table: `zid_migrations.alembic_version__session`.

May create or alter **only** these tables (enforced by CI against `backend/database/table-ownership.yaml`):

- `authn.session`
- `authn.oauth_client`
- `authn.refresh_token_family`
