# Migrations: `verification` module

Alembic stream owned by the **verification** module (zoiko-id-platform, DEC-D6). Version table: `zid_migrations.alembic_version__verification`.

May create or alter **only** these tables (enforced by CI against `backend/database/table-ownership.yaml`):

- `verify.verification_assertion`
- `verify.assurance_profile`
- `verify.provider_adapter`
- `verify.verification_session`
- `verify.provider_callback`
- `verify.review_case`
- `verify.review_decision`
- `verify.reliance_decision`
