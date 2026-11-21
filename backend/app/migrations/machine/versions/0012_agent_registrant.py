"""Who registered an agent (CR-142).

Revision ID: machine_0012
Revises: machine_0011
Create Date: 2026-11-20

An agent's sponsor, operator and publisher are the people who answer for it. The person who registered it may be none of them, so activation could not
tell that the person asking to switch it on had built it. The registrant is stored here, set once at registration. It is nullable because an agent
registered before this column has no recorded registrant on the record (the audit trail still names who did it). The rule treats an unknown registrant as no one.
"""

from zid_platform.migrations import execute_script as _run

revision = "machine_0012"
down_revision = "machine_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE agent.agent_profile ADD COLUMN registered_by uuid;
        COMMENT ON COLUMN agent.agent_profile.registered_by IS
            'the principal who registered the agent; set once at registration; null for an agent registered before CR-142';
    """)


def downgrade() -> None:
    _run("ALTER TABLE agent.agent_profile DROP COLUMN registered_by;")
