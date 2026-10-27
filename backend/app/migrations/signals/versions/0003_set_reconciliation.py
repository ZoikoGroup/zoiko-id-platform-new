"""Reconcile outbound SETs that were never acknowledged.

Revision ID: signals_0003
Revises: signals_0002
Create Date: 2026-10-26

ZID-SIGNALS-001 §15 (SIG-25): a SET the receiver never acknowledged inside its propagation SLO is detected and
replaced by a freshly built one; the original is marked superseded so it is neither handed out again nor counted
as pending.
"""

from zid_platform.migrations import execute_script as _run

revision = "signals_0003"
down_revision = "signals_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE signals.ssf_outbound_set
            ADD COLUMN superseded_at timestamptz,
            ADD COLUMN repair_of text,
            ADD COLUMN repaired_at timestamptz;
        CREATE INDEX ssf_outbound_open ON signals.ssf_outbound_set (stream_id, created_at)
            WHERE acked_at IS NULL AND superseded_at IS NULL;
    """)


def downgrade() -> None:
    _run("""
        DROP INDEX signals.ssf_outbound_open;
        ALTER TABLE signals.ssf_outbound_set DROP COLUMN superseded_at, DROP COLUMN repair_of, DROP COLUMN repaired_at;
    """)
