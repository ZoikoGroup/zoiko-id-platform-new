"""Alerts and tasks (ZID-UX-001 §20; CR-019).

Revision ID: notification_0001
Revises:
Create Date: 2026-10-24

`platform.notification_request` holds the tenant's in-app alerts and tasks:
- severity CRITICAL / HIGH / MEDIUM / INFO (UX §20) and a state OPEN -> ACKNOWLEDGED -> RESOLVED, so an
  acknowledgement is never mistaken for a resolution;
- `correlation_key`: repeated correlated alerts collapse into one open row (occurrence count, last seen)
  instead of flooding the inbox;
- `mandatory`: notices that notification settings can never suppress.
Titles and bodies are platform-authored text from fixed templates; they never carry personal data, only
ids and object references. Tenant-scoped under RLS.
"""

from zid_platform.migrations import execute_script as _run

revision = "notification_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.notification_request (
            alert_id          uuid PRIMARY KEY,
            tenant_id         uuid NOT NULL,
            severity          text NOT NULL CHECK (severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'INFO')),
            kind              text NOT NULL CHECK (length(kind) BETWEEN 3 AND 120),
            title             text NOT NULL CHECK (length(title) BETWEEN 3 AND 200),
            body              text NOT NULL DEFAULT '' CHECK (length(body) <= 1000),
            object_type       text NOT NULL DEFAULT '',
            object_ref        text NOT NULL DEFAULT '',
            href              text NOT NULL DEFAULT '' CHECK (href = '' OR href LIKE '/%'),
            recommended_action text NOT NULL DEFAULT '',
            is_task           boolean NOT NULL DEFAULT false,
            mandatory         boolean NOT NULL DEFAULT false,
            state             text NOT NULL DEFAULT 'OPEN' CHECK (state IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')),
            correlation_key   text NOT NULL,
            occurrences       integer NOT NULL DEFAULT 1 CHECK (occurrences >= 1),
            source_event_id   uuid,
            first_seen_at     timestamptz NOT NULL DEFAULT now(),
            last_seen_at      timestamptz NOT NULL DEFAULT now(),
            acknowledged_by   uuid,
            acknowledged_at   timestamptz,
            resolved_by       uuid,
            resolved_at       timestamptz,
            CHECK (state <> 'ACKNOWLEDGED' OR acknowledged_at IS NOT NULL),
            CHECK (state <> 'RESOLVED' OR resolved_at IS NOT NULL)
        );
        -- one live row per correlated alert: a repeat bumps `occurrences` instead of adding a row
        CREATE UNIQUE INDEX notification_request_live ON platform.notification_request (tenant_id, correlation_key)
            WHERE state <> 'RESOLVED';
        CREATE INDEX notification_request_inbox ON platform.notification_request (tenant_id, state, last_seen_at DESC);

        ALTER TABLE platform.notification_request ENABLE ROW LEVEL SECURITY;
        ALTER TABLE platform.notification_request FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON platform.notification_request
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("DROP TABLE platform.notification_request;")
