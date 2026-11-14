"""A pending verification session is re-evaluated when the policy that governs its region changes (conformance T40; CR-092).

Revision ID: verification_0014
Revises: verification_0013
Create Date: 2026-11-14

`verify.verification_session.policy_review` records the last re-evaluation of a session that had not finished when the governing policy pack
of its region changed or the provider behind it was suspended: a key naming what governed then (so the same change is evaluated once), the result,
what governed at the start and what governs now. `regulatory_basis` stays what it is: written once, the rules in force when the session began.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0014"
down_revision = "verification_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("ALTER TABLE verify.verification_session ADD COLUMN policy_review jsonb;")


def downgrade() -> None:
    _run("ALTER TABLE verify.verification_session DROP COLUMN policy_review;")
