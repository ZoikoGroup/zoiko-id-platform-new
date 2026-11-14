"""A lost issuer or accreditation status flags the proofs issued under it for review (conformance T42; CR-092).

Revision ID: credential_0014
Revises: credential_0013
Create Date: 2026-11-14

claim_monitor already says what to re-ask a source about and when. Three nullable columns say that a current proof was flagged for review because
the issuer or accreditation body behind it lost its status: which statement caused it (so one statement flags a proof once), why (a code), and
when. Nothing else changes: row-level security and grants are those of the table.
"""

from zid_platform.migrations import execute_script as _run

revision = "credential_0014"
down_revision = "credential_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE cred.claim_monitor
            ADD COLUMN review_statement_id text,
            ADD COLUMN review_reason text,
            ADD COLUMN review_requested_at timestamptz;
    """)


def downgrade() -> None:
    _run("""
        ALTER TABLE cred.claim_monitor
            DROP COLUMN review_statement_id,
            DROP COLUMN review_reason,
            DROP COLUMN review_requested_at;
    """)
