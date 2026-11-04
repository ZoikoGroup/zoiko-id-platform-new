"""Stacked promotions on a quote line (ZID-COM-CL-003 T03).

Revision ID: commercial_0020
Revises: commercial_0019
Create Date: 2026-11-04

A line can carry the discount it was quoted at and promotions on top of it. Approval authority is chosen on the combined economics, never on each part
alone, so two 5% promotions are an effective 9.75%, not two things that each fit the account executive's band. The promotions are part of the quote's
digest. quote_line stays immutable (its trigger is unchanged).
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0020"
down_revision = "commercial_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE commercial.quote_line ADD COLUMN promotions numeric(7,6)[] NOT NULL DEFAULT '{}';
    """)
