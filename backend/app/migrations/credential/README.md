# Migrations: `credential` module

Alembic stream owned by the **credential** module (zoiko-id-platform, DEC-D6). Version table: `zid_migrations.alembic_version__credential`.

May create or alter **only** these tables (enforced by CI against `backend/database/table-ownership.yaml`):

- `cred.credential_record`
- `cred.presentation_record`
- `cred.credential_configuration`
- `cred.issuer_trust`
- `cred.rp_registration`
- `cred.status_list`
