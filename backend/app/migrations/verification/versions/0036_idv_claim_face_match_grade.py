"""A claim grade for an approval on a live face against a document photograph (ZID-DTV-WF-03; CR-129).

Revision ID: verification_0036
Revises: verification_0035
Create Date: 2026-11-19

FACE_MATCH_NO_LIVENESS: the owner-acknowledged face-match route approved the case because a live face looked like the photograph printed on an uploaded document. No liveness test ran and
nothing established that the document is genuine, so it is the lowest grade, and the claim says so. Whether a consumer accepts it is the consumer's decision.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0036"
down_revision = "verification_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE verify.idv_case_claim DROP CONSTRAINT idv_case_claim_grade_check;
        ALTER TABLE verify.idv_case_claim ADD CONSTRAINT idv_case_claim_grade_check
            CHECK (grade IN ('CHIP_VERIFIED', 'REVIEWED_NO_CHIP', 'AUTOMATED_NO_CHIP', 'FACE_MATCH_NO_LIVENESS'));
    """)


def downgrade() -> None:
    _run("""
        ALTER TABLE verify.idv_case_claim DROP CONSTRAINT idv_case_claim_grade_check;
        ALTER TABLE verify.idv_case_claim ADD CONSTRAINT idv_case_claim_grade_check
            CHECK (grade IN ('CHIP_VERIFIED', 'REVIEWED_NO_CHIP', 'AUTOMATED_NO_CHIP'));
    """)
