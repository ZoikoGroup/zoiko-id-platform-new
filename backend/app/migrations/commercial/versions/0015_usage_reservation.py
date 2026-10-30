"""Usage reservations: reserve, commit or release a meter's quantity before the work happens (ZID-ENG-DEC-008 section 05; CR-045).

Revision ID: commercial_0015
Revises: commercial_0014
Create Date: 2026-10-30

usage_reservation: one row per (tenant, idempotency key). A retry, a provider callback or a concurrent request with the same key
is the same reservation, so nothing is consumed twice. A reservation moves RESERVED to COMMITTED or RELEASED once and never back;
quantity, meter, key and catalogue pin never change; nothing is deleted. It is the quota ledger of this API, not the billable
ledger: committing writes no invoice line, and no price, cap or overage is created here.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0015"
down_revision = "commercial_0014"
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE commercial.usage_reservation (
            reservation_id  uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            meter           text NOT NULL CHECK (meter ~ '^[a-z]+[.][a-z]+$'),
            quantity        bigint NOT NULL CHECK (quantity > 0 AND quantity <= 1000000000),
            idempotency_key text NOT NULL CHECK (char_length(idempotency_key) BETWEEN 8 AND 128),
            period          text NOT NULL CHECK (period ~ '^[0-9]{{4}}-[0-9]{{2}}$'),
            catalog_version text NOT NULL,
            state           text NOT NULL DEFAULT 'RESERVED' CHECK (state IN ('RESERVED', 'COMMITTED', 'RELEASED')),
            outcome         text CHECK (outcome IS NULL OR char_length(outcome) <= 64),
            created_at      timestamptz NOT NULL DEFAULT now(),
            resolved_at     timestamptz,
            CHECK ((state = 'RESERVED') = (resolved_at IS NULL)),
            UNIQUE (tenant_id, idempotency_key)
        );
        CREATE INDEX usage_reservation_meter_period
            ON commercial.usage_reservation (tenant_id, meter, period);

        ALTER TABLE commercial.usage_reservation ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.usage_reservation FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON commercial.usage_reservation
            USING ({TENANT}) WITH CHECK ({TENANT});

        CREATE FUNCTION commercial.usage_reservation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'usage reservations are never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.state <> 'RESERVED' THEN
                RAISE EXCEPTION 'a resolved reservation does not change' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.state = 'RESERVED'
               OR (NEW.reservation_id, NEW.tenant_id, NEW.meter, NEW.quantity, NEW.idempotency_key, NEW.period,
                   NEW.catalog_version, NEW.created_at)
                  IS DISTINCT FROM
                  (OLD.reservation_id, OLD.tenant_id, OLD.meter, OLD.quantity, OLD.idempotency_key, OLD.period,
                   OLD.catalog_version, OLD.created_at) THEN
                RAISE EXCEPTION 'a reservation only resolves; its terms do not change' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER usage_reservation_guard
            BEFORE UPDATE OR DELETE ON commercial.usage_reservation
            FOR EACH ROW EXECUTE FUNCTION commercial.usage_reservation_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE commercial.usage_reservation;
        DROP FUNCTION commercial.usage_reservation_guard();
    """)
