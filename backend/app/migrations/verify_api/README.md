# Migrations: `verify_api` module

Alembic stream owned by the **verify_api** module (zoiko-id-platform; CR-149). Version table: `zid_migrations.alembic_version__verify_api`.

May create or alter **only** these tables (enforced by CI against `backend/database/table-ownership.yaml`):

- `verify.api_application`
- `verify.api_credential`
- `verify.api_access_token`

- `verify.api_session`
- `verify.api_decision`
- `verify.api_idempotency`

Run: `python -m zid_platform.migrations upgrade --url ... --stream verify_api`
