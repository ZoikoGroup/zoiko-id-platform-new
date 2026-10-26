"""Rating runs and rated usage (ZID-COM-001 §7, §11, §13, §30; CR-026).

Revision ID: commercial_0003
Revises: commercial_0002
Create Date: 2026-10-24

A `rating_run` turns one tenant's frozen month into priced lines. Its `inputs_hash` covers everything the
result depends on (frozen window hashes, approved adjustments, entitlement segments, the price book version,
connection counts and credits) and its `outputs_hash` covers the lines; running again on the same inputs must
give the same outputs, which is the replay proof. At most one run per tenant and month is COMPUTED; a corrected
re-run supersedes it and the old lines stay. `rated_usage` rows are insert-only. Money is numeric, never float.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0003"
down_revision = "commercial_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE commercial.rating_run (
            run_id         uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            period         date NOT NULL CHECK (period = date_trunc('month', period)::date),
            attempt        integer NOT NULL CHECK (attempt >= 1),
            state          text NOT NULL CHECK (state IN ('COMPUTED', 'SUPERSEDED')),
            currency       text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
            subtotal       numeric(18, 6) NOT NULL CHECK (subtotal >= 0),
            credit_balance numeric(18, 6) NOT NULL CHECK (credit_balance >= 0),
            inputs_hash    text NOT NULL CHECK (length(inputs_hash) = 64),
            outputs_hash   text NOT NULL CHECK (length(outputs_hash) = 64),
            warnings       jsonb NOT NULL DEFAULT '[]'::jsonb,
            actor          uuid,
            created_at     timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, period, attempt)
        );
        CREATE UNIQUE INDEX rating_run_one_computed ON commercial.rating_run (tenant_id, period)
            WHERE state = 'COMPUTED';

        CREATE TABLE commercial.rated_usage (
            line_id      uuid PRIMARY KEY,
            run_id       uuid NOT NULL REFERENCES commercial.rating_run (run_id),
            tenant_id    uuid NOT NULL,
            period       date NOT NULL,
            seq          integer NOT NULL CHECK (seq >= 1),
            kind         text NOT NULL CHECK (kind IN ('FEE', 'OVERAGE', 'CONNECTION', 'MINIMUM', 'CREDIT')),
            sku          text,
            meter        text,
            plan_code    text,
            segment      integer NOT NULL CHECK (segment >= 0),
            included     numeric(30, 10),
            chargeable   numeric(30, 10),
            unit_rate    numeric(18, 6),
            unit_divisor integer,
            units        numeric(30, 10),
            formula      text NOT NULL,
            net          numeric(18, 6) NOT NULL,
            currency     text NOT NULL,
            reverses     text,
            UNIQUE (run_id, seq),
            CHECK ((kind = 'CREDIT') = (net < 0) OR net = 0)
        );

        CREATE FUNCTION commercial.rating_run_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'rating runs are never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.state = 'COMPUTED' AND NEW.state = 'SUPERSEDED'
               AND (NEW.run_id, NEW.tenant_id, NEW.period, NEW.attempt, NEW.currency, NEW.subtotal,
                    NEW.credit_balance, NEW.inputs_hash, NEW.outputs_hash, NEW.warnings, NEW.actor, NEW.created_at)
                IS NOT DISTINCT FROM
                   (OLD.run_id, OLD.tenant_id, OLD.period, OLD.attempt, OLD.currency, OLD.subtotal,
                    OLD.credit_balance, OLD.inputs_hash, OLD.outputs_hash, OLD.warnings, OLD.actor, OLD.created_at) THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'a rating run can only be superseded' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER rating_run_guard BEFORE UPDATE OR DELETE ON commercial.rating_run
            FOR EACH ROW EXECUTE FUNCTION commercial.rating_run_guard();
        CREATE TRIGGER rated_usage_immutable BEFORE UPDATE OR DELETE ON commercial.rated_usage
            FOR EACH ROW EXECUTE FUNCTION commercial.immutable_guard();

        ALTER TABLE commercial.rating_run ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.rating_run FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON commercial.rating_run
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        ALTER TABLE commercial.rated_usage ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.rated_usage FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON commercial.rated_usage
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE commercial.rated_usage;
        DROP TABLE commercial.rating_run;
        DROP FUNCTION commercial.rating_run_guard();
    """)
