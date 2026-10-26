"""Billable metering ledger (ZID-COM-001 §7, §11, §26, §31; CR-025).

Revision ID: commercial_0002
Revises: commercial_0001
Create Date: 2026-10-24

`billable_event` is the economic record: one insert-only row per chargeable operation, written in the same
transaction as the operation itself and unique per (tenant, deduplication key), so a retry or redelivery
can never charge twice and a rolled-back operation leaves nothing. Corrections are separate
`billable_adjustment` rows that point at the event they correct; the original is never edited. A
`meter_window` row freezes one tenant's meter for one UTC month with a count and hash total, after which
the database refuses new events for that window. `mah_activity` is the once-per-month marker that makes a
monthly active human count once. All four are tenant-scoped under RLS. Events carry no names, emails or
claim values; only the MAH marker keeps a (pseudonymous) principal id.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0002"
down_revision = "commercial_0001"
branch_labels = None
depends_on = None

METERS = "('MAH', 'AUTH_EVENT', 'POLICY_DECISION', 'M2M_GRANT', 'VC_ISSUED', 'VC_PRESENTED')"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE commercial.billable_event (
            event_id             uuid PRIMARY KEY,
            tenant_id            uuid NOT NULL,
            deduplication_key    text NOT NULL CHECK (length(deduplication_key) BETWEEN 3 AND 200),
            meter_id             text NOT NULL CHECK (meter_id IN {METERS}),
            quantity             numeric(18, 6) NOT NULL CHECK (quantity > 0),
            occurred_at          timestamptz NOT NULL,
            received_at          timestamptz NOT NULL DEFAULT now(),
            window_month         date NOT NULL CHECK (window_month = date_trunc('month', window_month)::date),
            late                 boolean NOT NULL DEFAULT false,
            recovered            boolean NOT NULL DEFAULT false,
            commercial_account_id uuid,
            environment_id       text NOT NULL,
            principal_class      text CHECK (principal_class IN ('HUMAN', 'NON_HUMAN')),
            region               text NOT NULL,
            source_request_id    text,
            sku_version          integer,
            agreement_version    integer,
            privacy_classification text NOT NULL DEFAULT 'INTERNAL',
            row_hash             text NOT NULL CHECK (length(row_hash) = 64),
            UNIQUE (tenant_id, deduplication_key)
        );
        CREATE INDEX billable_event_window ON commercial.billable_event (tenant_id, window_month, meter_id);

        CREATE TABLE commercial.billable_adjustment (
            adjustment_id   uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            adjusts_event_id uuid NOT NULL REFERENCES commercial.billable_event (event_id),
            meter_id        text NOT NULL CHECK (meter_id IN {METERS}),
            window_month    date NOT NULL,
            quantity_delta  numeric(18, 6) NOT NULL CHECK (quantity_delta <> 0),
            reason_code     text NOT NULL CHECK (reason_code ~ '^[A-Z][A-Z0-9_]{{2,40}}$'),
            state           text NOT NULL DEFAULT 'REQUESTED' CHECK (state IN ('REQUESTED', 'APPROVED', 'REJECTED')),
            requested_by    uuid NOT NULL,
            requested_at    timestamptz NOT NULL DEFAULT now(),
            approved_by     uuid,
            decided_at      timestamptz,
            CHECK (approved_by IS NULL OR approved_by <> requested_by),
            CHECK ((state = 'REQUESTED') = (approved_by IS NULL))
        );
        CREATE INDEX billable_adjustment_window ON commercial.billable_adjustment (tenant_id, window_month, meter_id);

        CREATE TABLE commercial.meter_window (
            tenant_id      uuid NOT NULL,
            meter_id       text NOT NULL CHECK (meter_id IN {METERS}),
            window_month   date NOT NULL CHECK (window_month = date_trunc('month', window_month)::date),
            frozen_at      timestamptz NOT NULL DEFAULT now(),
            event_count    bigint NOT NULL CHECK (event_count >= 0),
            quantity_total numeric(20, 6) NOT NULL,
            hash_total     text NOT NULL CHECK (length(hash_total) = 64),
            PRIMARY KEY (tenant_id, meter_id, window_month)
        );

        CREATE TABLE commercial.mah_activity (
            tenant_id     uuid NOT NULL,
            window_month  date NOT NULL CHECK (window_month = date_trunc('month', window_month)::date),
            principal_id  uuid NOT NULL,
            first_seen_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, window_month, principal_id)
        );

        CREATE FUNCTION commercial.billable_event_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF EXISTS (
                    SELECT 1 FROM commercial.meter_window w
                    WHERE w.tenant_id = NEW.tenant_id AND w.meter_id = NEW.meter_id
                      AND w.window_month = NEW.window_month
                ) THEN
                    RAISE EXCEPTION 'meter window frozen' USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'billable events are insert-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER billable_event_guard BEFORE INSERT OR UPDATE OR DELETE ON commercial.billable_event
            FOR EACH ROW EXECUTE FUNCTION commercial.billable_event_guard();

        CREATE FUNCTION commercial.immutable_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is immutable', TG_TABLE_NAME USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER meter_window_immutable BEFORE UPDATE OR DELETE ON commercial.meter_window
            FOR EACH ROW EXECUTE FUNCTION commercial.immutable_guard();
        CREATE TRIGGER mah_activity_immutable BEFORE UPDATE OR DELETE ON commercial.mah_activity
            FOR EACH ROW EXECUTE FUNCTION commercial.immutable_guard();

        CREATE FUNCTION commercial.adjustment_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'adjustments are never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.state <> 'REQUESTED' THEN
                RAISE EXCEPTION 'a decided adjustment is final' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.adjustment_id, NEW.tenant_id, NEW.adjusts_event_id, NEW.meter_id, NEW.window_month,
                NEW.quantity_delta, NEW.reason_code, NEW.requested_by, NEW.requested_at)
               IS DISTINCT FROM
               (OLD.adjustment_id, OLD.tenant_id, OLD.adjusts_event_id, OLD.meter_id, OLD.window_month,
                OLD.quantity_delta, OLD.reason_code, OLD.requested_by, OLD.requested_at) THEN
                RAISE EXCEPTION 'an adjustment can only be decided' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER adjustment_guard BEFORE UPDATE OR DELETE ON commercial.billable_adjustment
            FOR EACH ROW EXECUTE FUNCTION commercial.adjustment_guard();

        ALTER TABLE commercial.billable_event ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.billable_event FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON commercial.billable_event
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        ALTER TABLE commercial.billable_adjustment ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.billable_adjustment FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON commercial.billable_adjustment
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        ALTER TABLE commercial.meter_window ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.meter_window FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON commercial.meter_window
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        ALTER TABLE commercial.mah_activity ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.mah_activity FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON commercial.mah_activity
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE commercial.mah_activity;
        DROP TABLE commercial.meter_window;
        DROP TABLE commercial.billable_adjustment;
        DROP TABLE commercial.billable_event;
        DROP FUNCTION commercial.adjustment_guard();
        DROP FUNCTION commercial.immutable_guard();
        DROP FUNCTION commercial.billable_event_guard();
    """)
