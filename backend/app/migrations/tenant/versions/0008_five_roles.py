"""The five tenant roles of ZID-ENG-DEC-007 (slice L-7; CR-044).

Revision ID: tenant_0008
Revises: tenant_0007
Create Date: 2026-10-30

Adds APPROVER, OPERATOR and VIEWER beside OWNER, ADMIN and MEMBER. MEMBER stays: it is what a provisioning feed creates and it holds
no matrix permission beyond its own account. Nothing else changes: a role is a label, and authority still comes from grants.
"""

from zid_platform.migrations import execute_script as _run

revision = "tenant_0008"
down_revision = "tenant_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE platform.tenant_role_assignment DROP CONSTRAINT tenant_role_assignment_role_check;
        ALTER TABLE platform.tenant_role_assignment ADD CONSTRAINT tenant_role_assignment_role_check
            CHECK (role IN ('OWNER', 'ADMIN', 'APPROVER', 'OPERATOR', 'VIEWER', 'MEMBER'));
    """)
