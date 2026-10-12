"""Transactional outbox and consumer inbox (ZID-EVENTS-001 §2, §5, §7-8, §10; DEC-D2).

Revision ID: outbox_0001
Revises:
Create Date: 2026-10-12

- outbox_event: written in the same transaction as the domain change (no dual write); the event id is
  fixed before commit and reused on every publish retry; one row per (aggregate, version), so a
  producer can't emit two different events for the same aggregate version.
- inbox_message: one row per (consumer, event id) makes every consumer idempotent; the highest
  processed aggregate_version per consumer/aggregate lets ordered consumers detect gaps.
- Events are immutable: only publication bookkeeping columns may change after insert.
Writers are tenant-scoped by RLS; the relay runs as a dedicated role with BYPASSRLS (deploy grants).
"""

from zid_platform.migrations import execute_script as _run

revision = "outbox_0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = ("outbox_event", "inbox_message")


def upgrade() -> None:
    _run("""
        CREATE TABLE events.outbox_event (
            event_id            uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            cell_id             text NOT NULL,
            aggregate_type      text NOT NULL,
            aggregate_id        text NOT NULL,
            aggregate_version   bigint NOT NULL CHECK (aggregate_version >= 0),
            event_type          text NOT NULL CHECK (event_type ~ '^com\\.zoiko\\.id\\.[a-z0-9_.]+\\.v[0-9]+$'),
            schema_id           text NOT NULL,
            subject             text NOT NULL,
            lane                text NOT NULL CHECK (lane IN ('priority', 'domain', 'evidence', 'integration')),
            classification      text NOT NULL CHECK (classification IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')),
            payload             jsonb NOT NULL,
            correlation_id      text NOT NULL,
            causation_id        text,
            occurred_at         timestamptz NOT NULL,
            created_at          timestamptz NOT NULL DEFAULT now(),
            lease_until         timestamptz,
            attempts            integer NOT NULL DEFAULT 0,
            published_at        timestamptz,
            quarantined_reason  text,
            CHECK (octet_length(payload::text) <= 262144),
            UNIQUE (aggregate_type, aggregate_id, aggregate_version)
        );
        CREATE INDEX outbox_unpublished ON events.outbox_event (lane, created_at)
            WHERE published_at IS NULL AND quarantined_reason IS NULL;

        CREATE FUNCTION events.outbox_event_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.event_id, NEW.tenant_id, NEW.aggregate_type, NEW.aggregate_id, NEW.aggregate_version,
                NEW.event_type, NEW.payload, NEW.occurred_at, NEW.subject)
               IS DISTINCT FROM
               (OLD.event_id, OLD.tenant_id, OLD.aggregate_type, OLD.aggregate_id, OLD.aggregate_version,
                OLD.event_type, OLD.payload, OLD.occurred_at, OLD.subject) THEN
                RAISE EXCEPTION 'events are immutable; emit a correcting event' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER outbox_event_immutable BEFORE UPDATE ON events.outbox_event
            FOR EACH ROW EXECUTE FUNCTION events.outbox_event_immutable();

        CREATE TABLE events.inbox_message (
            consumer            text NOT NULL,
            event_id            uuid NOT NULL,
            tenant_id           uuid NOT NULL,
            aggregate_type      text NOT NULL,
            aggregate_id        text NOT NULL,
            aggregate_version   bigint NOT NULL,
            status              text NOT NULL CHECK (status IN ('PROCESSED', 'QUARANTINED')),
            reason              text,
            first_seen_at       timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (consumer, event_id)
        );
        CREATE INDEX inbox_aggregate ON events.inbox_message (consumer, aggregate_type, aggregate_id, aggregate_version);
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
        DROP TABLE events.inbox_message;
        DROP TRIGGER outbox_event_immutable ON events.outbox_event;
        DROP FUNCTION events.outbox_event_immutable();
        DROP TABLE events.outbox_event;
    """)
