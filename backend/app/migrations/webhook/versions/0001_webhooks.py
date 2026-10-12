"""Webhook endpoints and deliveries (ZID-EVENTS-001 §14-20).

Revision ID: webhook_0001
Revises:
Create Date: 2026-10-12

webhook_endpoint carries its subscription (event types, status, signing key id, max_inflight); an
endpoint delivers only once verified (ACTIVE). webhook_delivery is an immutable payload snapshot per
(endpoint, event) with attempt bookkeeping; replays create a new delivery row with the same event id.
"""

from zid_platform.migrations import execute_script as _run

revision = "webhook_0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = ("webhook_endpoint", "webhook_delivery")


def upgrade() -> None:
    _run("""
        CREATE TABLE events.webhook_endpoint (
            endpoint_id         uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            url                 text NOT NULL CHECK (url LIKE 'https://%' OR url LIKE 'http://%'),
            event_types         text[] NOT NULL CHECK (cardinality(event_types) > 0),
            status              text NOT NULL CHECK (status IN ('PENDING_VERIFICATION', 'ACTIVE', 'PAUSED',
                                                                 'DISABLED', 'RETIRED')),
            signing_key_id      text NOT NULL,
            verification_token  text,
            max_inflight        integer NOT NULL DEFAULT 4 CHECK (max_inflight BETWEEN 1 AND 64),
            created_by          uuid NOT NULL,
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE events.webhook_delivery (
            delivery_id         uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            endpoint_id         uuid NOT NULL REFERENCES events.webhook_endpoint (endpoint_id),
            event_id            uuid NOT NULL,
            event_type          text NOT NULL,
            payload             jsonb NOT NULL,
            payload_digest      text NOT NULL,
            status              text NOT NULL CHECK (status IN ('QUEUED', 'IN_FLIGHT', 'RETRY_SCHEDULED', 'DELIVERED',
                                                                 'FAILED', 'EXHAUSTED')),
            attempts            integer NOT NULL DEFAULT 0,
            next_attempt_at     timestamptz NOT NULL DEFAULT now(),
            last_status_code    integer,
            last_error          text,
            replay_of           uuid REFERENCES events.webhook_delivery (delivery_id),
            created_at          timestamptz NOT NULL DEFAULT now(),
            delivered_at        timestamptz
        );
        -- one original delivery per (endpoint, event); replays reference their original
        CREATE UNIQUE INDEX delivery_once_per_event ON events.webhook_delivery (endpoint_id, event_id)
            WHERE replay_of IS NULL;
        CREATE INDEX delivery_due ON events.webhook_delivery (next_attempt_at)
            WHERE status IN ('QUEUED', 'RETRY_SCHEDULED');

        CREATE FUNCTION events.webhook_delivery_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.payload, NEW.payload_digest, NEW.event_id, NEW.endpoint_id)
               IS DISTINCT FROM (OLD.payload, OLD.payload_digest, OLD.event_id, OLD.endpoint_id) THEN
                RAISE EXCEPTION 'delivery payload snapshots are immutable' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER webhook_delivery_snapshot BEFORE UPDATE ON events.webhook_delivery
            FOR EACH ROW EXECUTE FUNCTION events.webhook_delivery_snapshot();
    """)
    for table in TENANT_TABLES:
        _run(f"""
            ALTER TABLE events.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE events.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON events.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TRIGGER webhook_delivery_snapshot ON events.webhook_delivery;
        DROP FUNCTION events.webhook_delivery_snapshot();
        DROP TABLE events.webhook_delivery;
        DROP TABLE events.webhook_endpoint;
    """)
