"""A recheck links to the session it re-examines (ZID-EXT-IDV-001 epic E16, WF-12; CR-155).

Revision ID: verify_api_0004
Revises: verify_api_0003
Create Date: 2026-11-24

A recheck is a NEW session for the same customer reference, made after a result, with its own launch link and its own decision. `recheck_of` names the session it follows, so the history of one customer's checks can be
shown in order and a recheck is never mistaken for the original. The original and its decisions are never changed.
"""

from zid_platform.migrations import execute_script as _run

revision = "verify_api_0004"
down_revision = "verify_api_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE verify.api_session ADD COLUMN recheck_of text REFERENCES verify.api_session (session_id);
        CREATE INDEX api_session_rechecks ON verify.api_session (recheck_of) WHERE recheck_of IS NOT NULL;
    """)


def downgrade() -> None:
    _run("""
        DROP INDEX verify.api_session_rechecks;
        ALTER TABLE verify.api_session DROP COLUMN recheck_of;
    """)
