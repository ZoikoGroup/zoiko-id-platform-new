# Migrations: `assurance` module

Alembic stream owned by the **assurance** module (ZID-UAT-005; CR-074). Version table: `zid_migrations.alembic_version__assurance`.

May create or alter **only** the `evidence.uat_*` tables listed for module `assurance` in `backend/database/table-ownership.yaml`
(enforced by CI): the case baseline, fixtures and their revisions, runs, the append-only event chain, signed manifests, defects, release candidates,
signatures, rollbacks and the role register. All are tenant-scoped under row-level security; the event chain, manifests, signatures and rollbacks
are append-only by trigger.
