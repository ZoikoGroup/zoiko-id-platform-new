"""Scheduled reauthorization of biometric evidence (ZID-DTV-WF-04 BIO-20; CR-110).

Revision ID: privacy_0009
Revises: privacy_0008
Create Date: 2026-11-17

The worker flags an approved DPIA package once, when its approval comes within the reassessment window (and again if it has lapsed without a new
package), so the controller is told before the biometric method stops; `reassessment_flagged_at` records that it did, so the notice is not repeated
every pass. Expiry itself needs no flag: the release gate refuses an expired package at every session.
"""

from zid_platform.migrations import execute_script as _run

revision = "privacy_0009"
down_revision = "privacy_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE privacy.dpia_package ADD COLUMN reassessment_flagged_at timestamptz;
        CREATE INDEX dpia_package_reassessment ON privacy.dpia_package (approved_until) WHERE state = 'APPROVED';
    """)


def downgrade() -> None:
    _run("""
        DROP INDEX privacy.dpia_package_reassessment;
        ALTER TABLE privacy.dpia_package DROP COLUMN reassessment_flagged_at;
    """)
