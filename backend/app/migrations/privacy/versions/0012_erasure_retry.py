"""Automatic retry of a failed first-party erasure task (ZID-DTV-WF-04 QA-023; CR-117).

Revision ID: privacy_0012
Revises: privacy_0011
Create Date: 2026-11-18

A first-party disposal that failed (a store raised, or had no disposer yet) is retried with a growing delay, a bounded number of times. Each task records how many attempts were made and when the next
is due. A task that runs out of attempts stays FAILED, is never reported complete, and keeps showing in the oversight view as ERASURE_STUCK until a person acts.
"""

from zid_platform.migrations import execute_script as _run

revision = "privacy_0012"
down_revision = "privacy_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE privacy.erasure_task
            ADD COLUMN attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            ADD COLUMN next_attempt_at timestamptz;
    """)


def downgrade() -> None:
    _run("""
        ALTER TABLE privacy.erasure_task DROP COLUMN next_attempt_at, DROP COLUMN attempts;
    """)
