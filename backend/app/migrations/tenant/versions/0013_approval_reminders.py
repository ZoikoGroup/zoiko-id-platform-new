"""Reminders raised about approval requests that are about to expire (ZID-UX-WF-013C section 05; slice L-13, CR-053).

Revision ID: tenant_0013
Revises: tenant_0012
Create Date: 2026-11-03

approval_reminder: one row per request and reminder window, written when the reminder is raised, so that a worker that runs every few seconds
raises each reminder once. Append-only: it is a record that the reminder went out, never edited or removed.
"""

from zid_platform.migrations import execute_script as _run

revision = "tenant_0013"
down_revision = "tenant_0012"
branch_labels = None
depends_on = None

TABLES = ("approval_reminder",)
_RLS = """
    ALTER TABLE platform.{t} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE platform.{t} FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON platform.{t}
        USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
"""


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.approval_reminder (
            tenant_id     uuid NOT NULL,
            case_id       uuid NOT NULL,
            window_hours  integer NOT NULL CHECK (window_hours > 0),
            raised_at     timestamptz NOT NULL,
            PRIMARY KEY (case_id, window_hours)
        );
        CREATE FUNCTION platform.approval_reminder_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'approval reminders are append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER approval_reminder_append_only BEFORE UPDATE OR DELETE ON platform.approval_reminder
            FOR EACH ROW EXECUTE FUNCTION platform.approval_reminder_guard();
    """)
    for table in TABLES:
        _run(_RLS.format(t=table))


def downgrade() -> None:
    pass
