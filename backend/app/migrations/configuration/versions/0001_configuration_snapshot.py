"""Versioned tenant settings (ZID-UX-001 §19, §28 optimistic concurrency; ZID-DATA-001 §1.1; CR-022).

Revision ID: configuration_0001
Revises:
Create Date: 2026-10-24

`platform.configuration_snapshot` is an append-only history of each tenant's settings per area
(AUTHENTICATION, NOTIFICATIONS, BRANDING). The current value of an area is its highest revision. A write
names the revision it was based on (`expected_revision`); a concurrent change makes the primary key collide,
so the second writer gets a visible conflict instead of silently overwriting (UX-06). Rows are never updated
or deleted: a stale or mistaken setting is replaced by a newer revision and the old one stays as history.
Tenant-scoped under RLS. Values are validated, closed vocabularies and carry no personal data.
"""

from zid_platform.migrations import execute_script as _run

revision = "configuration_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.configuration_snapshot (
            tenant_id    uuid NOT NULL,
            area         text NOT NULL CHECK (area IN ('AUTHENTICATION', 'NOTIFICATIONS', 'BRANDING')),
            revision     integer NOT NULL CHECK (revision >= 1),
            value        jsonb NOT NULL,
            commitment   text NOT NULL,
            created_by   uuid NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, area, revision)
        );
        CREATE FUNCTION platform.configuration_snapshot_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'configuration history is append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER configuration_snapshot_append_only BEFORE UPDATE OR DELETE ON platform.configuration_snapshot
            FOR EACH ROW EXECUTE FUNCTION platform.configuration_snapshot_guard();

        ALTER TABLE platform.configuration_snapshot ENABLE ROW LEVEL SECURITY;
        ALTER TABLE platform.configuration_snapshot FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON platform.configuration_snapshot
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("""
        DROP TRIGGER configuration_snapshot_append_only ON platform.configuration_snapshot;
        DROP FUNCTION platform.configuration_snapshot_guard();
        DROP TABLE platform.configuration_snapshot;
    """)
