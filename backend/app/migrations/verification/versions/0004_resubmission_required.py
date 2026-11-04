"""A verification session can wait for the person to send the evidence again (ZID-EDR-005).

Revision ID: verification_0004
Revises: verification_0003
Create Date: 2026-11-04

RESUBMISSION_REQUIRED is its own state, not PENDING with a reason code: the person is asked to act, the session is still open and can still expire, and
the provider's next result moves it on. It is never a decision.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0004"
down_revision = "verification_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE verify.verification_session DROP CONSTRAINT IF EXISTS verification_session_status_check;
        ALTER TABLE verify.verification_session ADD CONSTRAINT verification_session_status_check
            CHECK (status IN ('CREATED', 'IN_PROGRESS', 'PENDING_EXTERNAL', 'RESUBMISSION_REQUIRED',
                              'MANUAL_REVIEW', 'VERIFIED', 'FAILED', 'CANCELED', 'EXPIRED'));
    """)
