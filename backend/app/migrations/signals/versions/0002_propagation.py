"""Measure propagation of outbound security events.

Revision ID: signals_0002
Revises: signals_0001
Create Date: 2026-10-13

ZID-SIGNALS-001 §15.1, §29 (FR-SIG-003/004): each queued SET remembers when the authoritative state
change committed and the risk floor it carries, so commit -> receiver acknowledgement can be
measured per stream against the R0-R3 propagation SLOs.
"""

from zid_platform.migrations import execute_script as _run

revision = "signals_0002"
down_revision = "signals_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE signals.ssf_outbound_set
            ADD COLUMN event_committed_at timestamptz,
            ADD COLUMN risk_floor smallint NOT NULL DEFAULT 1 CHECK (risk_floor BETWEEN 0 AND 3);
    """)


def downgrade() -> None:
    _run(
        "ALTER TABLE signals.ssf_outbound_set DROP COLUMN event_committed_at, DROP COLUMN risk_floor;"
    )
