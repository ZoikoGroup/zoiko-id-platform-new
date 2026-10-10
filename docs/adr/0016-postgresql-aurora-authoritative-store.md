# ADR-0016: Aurora PostgreSQL as the authoritative store, schema per module, RLS

- **Status:** Proposed
- **Owner:** Data Platform Lead (name to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** ADR-0002 §4 (PostgreSQL remains authority)

## Context
The Data Model spec makes PostgreSQL authoritative, with tenant isolation, bitemporal facts, RLS as defense in depth, and per-service roles without BYPASSRLS.

## Decision
- **Aurora PostgreSQL** per cell: one trust-kernel cluster at Stage A, plus separate clusters for SpiceDB, Temporal and evidence metadata.
- One schema per module, each with its own runtime role. Runtime roles never own tables. Migrations use `db_owner_migration`.
- `FORCE ROW LEVEL SECURITY` on tenant tables. `zid-db` sets the tenant and principal with `SET LOCAL` only, inside every transaction, after authentication. The pool resets connection state.
- Conventions: UUIDv7 keys, `tenant_id` on every row, row/security/relation revisions, bitemporal columns, and `numeric(38,9)` for money.
- **Ownership per table, not per schema:** Data Model schema names are kept as namespaces, but the Service spec gives tables in one schema to different services (for example `authz` holds AUTHY grants, POL releases and AUTHZ decisions). `backend/database/table-ownership.yaml` assigns exactly one owning module per table, and DB role grants are generated from it at table level.
- Alembic, **one stream per owning module** (`migrations/<module>/`, version table in the `zid_migrations` schema), expand → migrate → contract. Schemas are created once by `backend/database/schemas/`.
- PgBouncer in transaction mode. `SET LOCAL` is compatible with it.

## Consequences
- Aurora handles quorum storage across 3 AZs and fenced failover. Writer-epoch fencing above the database is still ours (ADR-0020).
- Stage B introduces tenant sharding within a cell.
