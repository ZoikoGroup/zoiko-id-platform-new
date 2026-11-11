"""Outreach to claim check: carry the question until it is answered (GID-054, CR-069).

Revision ID: credential_0007
Revises: credential_0006
Create Date: 2026-11-11

An issuer outreach asks "did you confer this award on this person?", so the request must remember who and what until the answer is
reviewed. `subject_principal_id` and `claim` (the fields asked about, the jurisdiction and the profession) hold that, and `claim` is set
to NULL the moment the outreach is concluded into a claim check, so the claimed values are not kept after they have done their job.
A check from an outreach is linked back through the existing `check_id` column.
"""

from zid_platform.migrations import execute_script as _run

revision = "credential_0007"
down_revision = "credential_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE cred.outreach_request
            ADD COLUMN subject_principal_id uuid,
            ADD COLUMN claim jsonb,
            ADD COLUMN concluded_at timestamptz,
            ADD CONSTRAINT outreach_claim_needs_subject CHECK (claim IS NULL OR subject_principal_id IS NOT NULL);
    """)


def downgrade() -> None:
    _run("""
        ALTER TABLE cred.outreach_request
            DROP CONSTRAINT outreach_claim_needs_subject,
            DROP COLUMN concluded_at,
            DROP COLUMN claim,
            DROP COLUMN subject_principal_id;
    """)
