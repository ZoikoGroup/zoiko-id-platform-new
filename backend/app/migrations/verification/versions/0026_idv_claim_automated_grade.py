"""A claim's grade can be the automated one (ZID-DTV-WF-03; CR-126).

Revision ID: verification_0026
Revises: verification_0025
Create Date: 2026-11-18

AUTOMATED_NO_CHIP: the platform approved the case itself from the document and the applicant's typed details (no chip, no reviewer, no face or authenticity check). Whether a consumer
accepts it is the consumer's decision.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0026"
down_revision = "verification_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE verify.idv_case_claim DROP CONSTRAINT idv_case_claim_grade_check;
        ALTER TABLE verify.idv_case_claim ADD CONSTRAINT idv_case_claim_grade_check
            CHECK (grade IN ('CHIP_VERIFIED', 'REVIEWED_NO_CHIP', 'AUTOMATED_NO_CHIP'));
    """)


def downgrade() -> None:
    _run("""
        ALTER TABLE verify.idv_case_claim DROP CONSTRAINT idv_case_claim_grade_check;
        ALTER TABLE verify.idv_case_claim ADD CONSTRAINT idv_case_claim_grade_check
            CHECK (grade IN ('CHIP_VERIFIED', 'REVIEWED_NO_CHIP'));
    """)
