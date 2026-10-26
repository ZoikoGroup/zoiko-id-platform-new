"""Verification decision basis: normalized facts a decision was derived from.

Revision ID: verification_0002
Revises: verification_0001
Create Date: 2026-10-25

ZID-VERIFY-001 §16 / VERIFY-29. `verify.verification_session.decision_basis` stores the
adapter-normalized facts of the decision (claims with method, evidence types and a commitment to
the value - never the value -, binding, liveness, fraud signals, the adapter and its mapping
version, the evidence commitment). With the pinned profile row and the assertions it lets
`domain.verification.decide` re-derive the recorded outcome (`reconstruct`).
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0002"
down_revision = "verification_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE verify.verification_session ADD COLUMN decision_basis jsonb;
    """)


def downgrade() -> None:
    _run("""
        ALTER TABLE verify.verification_session DROP COLUMN decision_basis;
    """)
