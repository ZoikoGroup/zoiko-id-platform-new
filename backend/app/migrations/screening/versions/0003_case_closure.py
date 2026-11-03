"""A screening case can be closed with its claim (ZID-ED-04 section 07; ZID-UX-WF-013B; slice L-12, CR-052).

Revision ID: screening_0003
Revises: screening_0002
Create Date: 2026-11-03

closed_at: the claim this case belongs to was withdrawn or abandoned, so nobody is watched for it any more: the worker neither reads the
register again nor screens the case again. The case and its evidence stay as they were, for the retention schedule to decide.
"""

from zid_platform.migrations import execute_script as _run

revision = "screening_0003"
down_revision = "screening_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("ALTER TABLE verify.screening_case ADD COLUMN closed_at timestamptz;")


def downgrade() -> None:
    pass
