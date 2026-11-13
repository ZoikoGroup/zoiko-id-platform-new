"""A runtime registry of what each provider may be used for (ZID-INT-002 section 08; `provider.entitlement_revoked`; CR-071).

Revision ID: verification_0006
Revises: verification_0005
Create Date: 2026-11-12

Until now an entitlement existed only as the shape the routing rules take. This stores the deployment's own record of one provider's exact
service, countries, purposes and processing regions for a period. REVOKED is final: a revoked entitlement is never reopened, a new
period is a new record. No credential, key or contract text is held here, only the scope and its status.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0006"
down_revision = "verification_0005"
branch_labels = None
depends_on = None

RULE = "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE verify.provider_entitlement (
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
        CREATE UNIQUE INDEX provider_entitlement_one_live
            ON verify.provider_entitlement (tenant_id, provider_id, service_code) WHERE status <> 'REVOKED';
        ALTER TABLE verify.provider_entitlement ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.provider_entitlement FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.provider_entitlement USING {RULE} WITH CHECK {RULE};
        CREATE FUNCTION verify.entitlement_revoked_is_final() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status = 'REVOKED' AND NEW.status <> 'REVOKED' THEN
                RAISE EXCEPTION 'a revoked entitlement is never reopened' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER entitlement_revoked_is_final BEFORE UPDATE ON verify.provider_entitlement
            FOR EACH ROW EXECUTE FUNCTION verify.entitlement_revoked_is_final();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.provider_entitlement;
        DROP FUNCTION verify.entitlement_revoked_is_final();
    """)
