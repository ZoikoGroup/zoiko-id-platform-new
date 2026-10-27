"""Incident evidence preservation entries (ZID-SRE-001 §17; OPS-20).

Revision ID: operations_0002
Revises: operations_0001
Create Date: 2026-10-26

An incident timeline gains an EVIDENCE entry: which artifacts were preserved and the chain-of-custody record that
covers them. A security or major incident cannot leave mitigation without one.
"""

from zid_platform.migrations import execute_script as _run

revision = "operations_0002"
down_revision = "operations_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE platform.incident_entry DROP CONSTRAINT incident_entry_kind_check;
        ALTER TABLE platform.incident_entry ADD CONSTRAINT incident_entry_kind_check CHECK (kind IN
            ('DECLARED', 'SEVERITY', 'STATUS', 'ROLE', 'ACTION', 'DECISION', 'COMMUNICATION', 'EVIDENCE',
             'RESOLVED', 'CLOSED'));
    """)


def downgrade() -> None:
    _run("""
        ALTER TABLE platform.incident_entry DROP CONSTRAINT incident_entry_kind_check;
        ALTER TABLE platform.incident_entry ADD CONSTRAINT incident_entry_kind_check CHECK (kind IN
            ('DECLARED', 'SEVERITY', 'STATUS', 'ROLE', 'ACTION', 'DECISION', 'COMMUNICATION', 'RESOLVED', 'CLOSED'));
    """)
