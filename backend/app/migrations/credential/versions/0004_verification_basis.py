"""Credential verification basis: what each decision rested on.

Revision ID: credential_0004
Revises: credential_0003
Create Date: 2026-10-25

ZID-VC-001 §28 (verification receipts), VC-27. `cred.presentation_record.verification_basis`
holds, for a verified presentation, the pinned adapter and configuration, the issuer trust
entries relied on (id, revision, scope, validity), the status observation and the holder-binding
result - never the credential, a disclosed value or an undisclosed claim - so the decision can be
re-derived later by the pure rules in `domain/credentials.py` (`replay_decision`).
"""

from zid_platform.migrations import execute_script as _run

revision = "credential_0004"
down_revision = "credential_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE cred.presentation_record ADD COLUMN verification_basis jsonb;
    """)


def downgrade() -> None:
    _run("""
        ALTER TABLE cred.presentation_record DROP COLUMN verification_basis;
    """)
