"""Agents expire: an agent may carry a date after which it stops acting (ZID-GAP-001 WP-10; MACHINE-001 §12).

Revision ID: machine_0006
Revises: machine_0005
Create Date: 2026-11-10

An agent that nobody reviews should not act for ever. `expires_at` is optional and only ever ends an agent's
authority: the lifecycle sweep suspends an ACTIVE agent whose date has passed (and the tool gateway refuses it
at once, before the sweep runs). Nothing is backfilled; an agent without a date behaves exactly as before.
"""

from zid_platform.migrations import execute_script as _run

revision = "machine_0006"
down_revision = "machine_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE agent.agent_profile ADD COLUMN expires_at timestamptz;
        CREATE INDEX agent_profile_expiry ON agent.agent_profile (expires_at)
            WHERE expires_at IS NOT NULL AND status = 'ACTIVE';
    """)


def downgrade() -> None:
    _run("""
        DROP INDEX agent.agent_profile_expiry;
        ALTER TABLE agent.agent_profile DROP COLUMN expires_at;
    """)
