# Migrations: `verify_analytics` module

Alembic stream owned by the **verify_analytics** module (ZID-CPS-WF-02 Command Center). Version table: `zid_migrations.alembic_version__verify_analytics`.

May create or alter **only** these tables (enforced by CI against `backend/database/table-ownership.yaml`):

- `metering.va_fact`: append-only analytics facts, one per source event, pseudonymous ids and codes only
- `metering.va_watermark`: per-source ingestion progress (freshness, late and rejected counts)
- `metering.va_correction`: the correction journal (requested and approved by two different people)
- `metering.va_snapshot`: frozen query results that exports and reruns reproduce
- `metering.va_export`: export jobs with a single-use download token (hash only)
- `metering.va_grant`: analytics permission bundles granted by an Owner
