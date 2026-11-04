"""A company claim can be submitted, withdrawn or abandoned (ZID-UX-WF-013B states SUBMITTED, WITHDRAWN, ABANDONED; slice L-12, CR-052).

Revision ID: organization_0007
Revises: organization_0006
Create Date: 2026-11-03

submitted_at: when the applicant said the claim is complete and ready for review (once; a repeat changes nothing).
closed_at, closed_reason: the applicant withdrew the claim, or it was left alone until it was abandoned. A closed claim is never refreshed,
screened again or reviewed; a verification that was reached stays in the history, and the applicant may start a new claim. A claim that
is verified is not closed by withdrawing it: that is a different decision, taken through the governed route.
"""

from zid_platform.migrations import execute_script as _run

revision = "organization_0007"
down_revision = "organization_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE org.kyb_case
            ADD COLUMN submitted_at  timestamptz,
            ADD COLUMN closed_at     timestamptz,
            ADD COLUMN closed_reason text CHECK (closed_reason IN ('WITHDRAWN', 'ABANDONED')),
            ADD CONSTRAINT kyb_case_closed_pair CHECK ((closed_at IS NULL) = (closed_reason IS NULL));
        CREATE INDEX kyb_case_open ON org.kyb_case (tenant_id, updated_at) WHERE closed_at IS NULL;
    """)


def downgrade() -> None:
    pass
