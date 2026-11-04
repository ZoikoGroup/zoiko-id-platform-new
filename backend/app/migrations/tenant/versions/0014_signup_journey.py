"""Which journey a person chose at sign-up, kept on their workspace (ZID-UX-WF-013A S5/S6; CR-059).

Revision ID: tenant_0014
Revises: tenant_0013
Create Date: 2026-11-04

`signup_journey` is PERSONAL (a personal workspace, nothing to verify) or ORGANIZATION (a provisional organization claim follows). It is set once by sign-up from the server's own record of the
request, never from anything the client sends, and it only decides what the dashboard offers first: it grants no role and no authority. Workspaces created before this column existed have none.
"""

from zid_platform.migrations import execute_script as _run

revision = "tenant_0014"
down_revision = "tenant_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE platform.tenant ADD COLUMN signup_journey text CHECK (signup_journey IN ('PERSONAL', 'ORGANIZATION'));
    """)


def downgrade() -> None:
    _run("ALTER TABLE platform.tenant DROP COLUMN signup_journey;")
