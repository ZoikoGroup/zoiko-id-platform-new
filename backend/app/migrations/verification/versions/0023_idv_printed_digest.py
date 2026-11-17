"""Salted digests of the printed holder data, kept only until the chip's data is compared (ZID-DTV-WF-03; CR-110).

Revision ID: verification_0023
Revises: verification_0022
Create Date: 2026-11-17

The printed machine-readable lines are read by the platform from the document photograph (never typed by the applicant). Only a salted digest of each normalised field is kept, so the comparison with the chip's
data group 1 can be made later without keeping the data itself; both columns are cleared as soon as the comparison is made. The existing guard trigger already allows these columns to change on an open case.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0023"
down_revision = "verification_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE verify.idv_case ADD COLUMN printed_salt text, ADD COLUMN printed_digests jsonb;
    """)


def downgrade() -> None:
    _run("ALTER TABLE verify.idv_case DROP COLUMN printed_salt, DROP COLUMN printed_digests;")
