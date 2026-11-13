"""Bridge: the tenant's runtime routing registry of what each provider may be used for (`verify.provider_route_entitlement`; CR-071).

Revision ID: verification_0013
Revises: verification_0012
Create Date: 2026-11-12

Two branches hit the same collision (two tables both wanted the name `verify.provider_entitlement`, and a second `verification_0005`) and fixed it in different ways:

- the shared branch created the routing registry as `verify.provider_entitlement` in `verification_0005b` and named the platform's append-only partner statements
  `verify.provider_contract_entitlement` (`verification_0008`);
- this branch (anil) created the routing registry as `verify.provider_route_entitlement` here, after 0012, and left the statements as `verify.provider_entitlement`.

The code uses the shared branch's name for the statements and this branch's name for the routing registry. This revision gives every database the routing registry under that name:

- it already has `provider_route_entitlement` (it came through this branch's old 0013): nothing to do;
- it came through 0005b: the registry is created and the rows of the old `provider_entitlement` are copied into it. Nothing is renamed or dropped here: the old table stays until a
  later, approved contract step removes it.

A database that was migrated on this branch's old chain and has not yet recorded 0013 would still hold the platform's partner statements under the old name `provider_entitlement`; there is
no such database at rest, and if one turns up its statements table must be renamed to `provider_contract_entitlement` by hand before this runs.

The downgrade does nothing: the earlier revisions own the tables.
"""

from alembic import op
from sqlalchemy import text
from zid_platform.migrations import execute_script as _run

revision = "verification_0013"
down_revision = "verification_0012"
branch_labels = None
depends_on = None

RULE = "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"


def _create() -> None:
    _run(f"""
        CREATE TABLE verify.provider_route_entitlement (
            entitlement_id     uuid PRIMARY KEY,
            tenant_id          uuid NOT NULL,
            provider_id        text NOT NULL CHECK (provider_id ~ '^[a-z0-9][a-z0-9_.-]{{1,62}}$'),
            service_code       text NOT NULL CHECK (service_code ~ '^[a-z0-9][a-z0-9_.-]{{1,62}}$'),
            countries          text[] NOT NULL CHECK (cardinality(countries) BETWEEN 1 AND 250),
            purposes           text[] NOT NULL CHECK (cardinality(purposes) BETWEEN 1 AND 40),
            processing_regions text[] NOT NULL CHECK (cardinality(processing_regions) BETWEEN 1 AND 20),
            valid_from         date NOT NULL,
            valid_until        date,
            assurance          integer NOT NULL CHECK (assurance BETWEEN 0 AND 10),
            cost_rank          integer NOT NULL DEFAULT 0 CHECK (cost_rank BETWEEN 0 AND 1000),
            status             text NOT NULL CHECK (status IN ('ACTIVE','SUSPENDED','REVOKED')),
            status_reason      text,
            created_by         uuid NOT NULL,
            created_at         timestamptz NOT NULL DEFAULT now(),
            status_changed_at  timestamptz NOT NULL DEFAULT now(),
            CHECK (valid_until IS NULL OR valid_until >= valid_from)
        );
        CREATE UNIQUE INDEX provider_route_entitlement_one_live
            ON verify.provider_route_entitlement (tenant_id, provider_id, service_code) WHERE status <> 'REVOKED';
        ALTER TABLE verify.provider_route_entitlement ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.provider_route_entitlement FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.provider_route_entitlement USING {RULE} WITH CHECK {RULE};
        CREATE FUNCTION verify.route_entitlement_revoked_is_final() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status = 'REVOKED' AND NEW.status <> 'REVOKED' THEN
                RAISE EXCEPTION 'a revoked entitlement is never reopened' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER route_entitlement_revoked_is_final BEFORE UPDATE ON verify.provider_route_entitlement
            FOR EACH ROW EXECUTE FUNCTION verify.route_entitlement_revoked_is_final();
    """)


def _has(name: str) -> bool:
    return bool(
        op.get_bind().execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": name}).scalar()
    )


def _has_column(table: str, column: str) -> bool:
    return bool(
        op.get_bind()
        .execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'verify' AND table_name = :t AND column_name = :c)"
            ),
            {"t": table, "c": column},
        )
        .scalar()
    )


def upgrade() -> None:
    if _has("verify.provider_route_entitlement"):
        return
    _create()
    if _has("verify.provider_entitlement") and _has_column("provider_entitlement", "provider_id"):
        # a database that came through 0005b holds its routing registry in the old table: copy it across (additive; the old table is left alone and is dropped
        # by a later, approved contract step)
        _run("""
            INSERT INTO verify.provider_route_entitlement (
                entitlement_id, tenant_id, provider_id, service_code, countries, purposes, processing_regions, valid_from, valid_until,
                assurance, cost_rank, status, status_reason, created_by, created_at, status_changed_at)
            SELECT entitlement_id, tenant_id, provider_id, service_code, countries, purposes, processing_regions, valid_from, valid_until,
                assurance, cost_rank, status, status_reason, created_by, created_at, status_changed_at
            FROM verify.provider_entitlement
        """)


def downgrade() -> None:
    pass
