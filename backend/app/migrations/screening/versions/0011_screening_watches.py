"""Ongoing screening of a subject: the watch and its per-source marks (ZID-DTV-WF-06 S12, ENG-14; CR-113).

Revision ID: screening_0011
Revises: screening_0010
Create Date: 2026-11-18

screening_watch: who is watched (by the case it rests on), for which classes and sources, on which triggers and at what cadence. A watch is created inactive and becomes active only when two different people
have approved it and the release gates are signed. screening_watch_mark keeps, per source, the last ATTEMPT and the last SUCCESS as two separate facts, so a scheduler that ran and failed is never shown as
monitoring, and "monitored continuously" is claimed only from a success inside the cadence.
"""

from zid_platform.migrations import execute_script as _run

revision = "screening_0011"
down_revision = "screening_0010"
branch_labels = None
depends_on = None

TABLES = ("screening_watch", "screening_watch_mark")
_RLS = """
    ALTER TABLE verify.{t} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE verify.{t} FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON verify.{t}
        USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
"""


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.screening_watch (
            watch_id         uuid PRIMARY KEY,
            tenant_id        uuid NOT NULL,
            request_id       uuid NOT NULL REFERENCES verify.screening_request (request_id),
            classes          jsonb NOT NULL,
            sources          jsonb NOT NULL,
            triggers         jsonb NOT NULL,
            cadence_seconds  integer NOT NULL CHECK (cadence_seconds BETWEEN 3600 AND 31536000),
            active           boolean NOT NULL DEFAULT false,
            approved_by      jsonb NOT NULL DEFAULT '[]',
            created_by       uuid NOT NULL,
            created_at       timestamptz NOT NULL,
            runs             integer NOT NULL DEFAULT 0 CHECK (runs >= 0),
            rescreen_requested_at timestamptz,
            CHECK (NOT active OR jsonb_array_length(approved_by) >= 2)
        );
        CREATE INDEX screening_watch_active ON verify.screening_watch (tenant_id) WHERE active;

        CREATE TABLE verify.screening_watch_mark (
            watch_id         uuid NOT NULL REFERENCES verify.screening_watch (watch_id),
            tenant_id        uuid NOT NULL,
            source_id        text NOT NULL,
            last_attempt_at  timestamptz,
            last_success_at  timestamptz,
            snapshot         text,
            PRIMARY KEY (watch_id, source_id)
        );
    """)
    for table in TABLES:
        _run(_RLS.format(t=table))
    _run(
        "CREATE POLICY reviewer_read ON verify.screening_watch FOR SELECT USING (current_setting('app.screening_reviewer', true) = 'on');"
    )


def downgrade() -> None:
    _run("DROP TABLE verify.screening_watch_mark, verify.screening_watch;")
