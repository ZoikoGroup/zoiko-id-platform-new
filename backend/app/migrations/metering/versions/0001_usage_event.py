"""API usage counters (ZID-UX-001 §18 usage and logs; ZID-DATA-001 §11.1; CR-023).

Revision ID: metering_0001
Revises:
Create Date: 2026-10-24

`metering.usage_event` holds hourly call counts per tenant, API surface, OAuth client and status class
(2xx / 3xx / 4xx / 5xx). It records how much an API was used and how it answered, never what was asked:
no paths with ids, no query strings, no bodies, no personal data. Counters are folded in by upsert
(`calls = calls + n`), so a flush can be retried without double counting a bucket's identity. Tenant-scoped
under RLS. The correction table of ZID-DATA-001 §11.1 is for billing and is not created here.
"""

from zid_platform.migrations import execute_script as _run

revision = "metering_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE metering.usage_event (
            tenant_id     uuid NOT NULL,
            bucket        timestamptz NOT NULL,
            surface       text NOT NULL CHECK (length(surface) BETWEEN 1 AND 40),
            client_id     text NOT NULL CHECK (length(client_id) BETWEEN 1 AND 128),
            status_class  text NOT NULL CHECK (status_class IN ('2xx', '3xx', '4xx', '5xx')),
            calls         bigint NOT NULL CHECK (calls >= 0),
            PRIMARY KEY (tenant_id, bucket, surface, client_id, status_class)
        );
        CREATE INDEX usage_event_recent ON metering.usage_event (tenant_id, bucket DESC);

        ALTER TABLE metering.usage_event ENABLE ROW LEVEL SECURITY;
        ALTER TABLE metering.usage_event FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON metering.usage_event
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("DROP TABLE metering.usage_event;")
