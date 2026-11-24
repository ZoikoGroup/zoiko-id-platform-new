"""Per-application workflow entitlement and hosted-page settings (ZID-EXT-IDV-001 epics E14 and E19; CR-156).

Revision ID: verify_api_0005
Revises: verify_api_0004
Create Date: 2026-11-24

`allowed_workflows` limits which approved workflows an application may start (an empty list means every approved workflow); `default_workflow_id` is used when a request names none. `branding` holds only the
approved, validated hosted-page settings (display name, one of a fixed set of themes, a support link, a language): never free-form styling, so a partner cannot make the hosted page look like something else.
"""

from zid_platform.migrations import execute_script as _run

revision = "verify_api_0005"
down_revision = "verify_api_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE verify.api_application
            ADD COLUMN allowed_workflows text[] NOT NULL DEFAULT '{}',
            ADD COLUMN default_workflow_id text,
            ADD COLUMN branding jsonb NOT NULL DEFAULT '{}';
    """)


def downgrade() -> None:
    _run("""
        ALTER TABLE verify.api_application
            DROP COLUMN branding,
            DROP COLUMN default_workflow_id,
            DROP COLUMN allowed_workflows;
    """)
