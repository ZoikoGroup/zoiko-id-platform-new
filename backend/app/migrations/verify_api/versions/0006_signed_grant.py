"""A production grant carries a signature over its record (ZID-EXT-IDV-001 epic E10, gate G4; CR-158).

Revision ID: verify_api_0006
Revises: verify_api_0005
Create Date: 2026-11-25

`signature` is a compact signed token over the grant's manifest and its digest, made with the go-live signing key when an operator approves. A grant made before this column existed has none (null) and is shown as
unsigned; a new grant cannot be made without one.
"""

from zid_platform.migrations import execute_script as _run

revision = "verify_api_0006"
down_revision = "verify_api_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE verify.api_production_grant ADD COLUMN signature text;
    """)


def downgrade() -> None:
    _run("""
        ALTER TABLE verify.api_production_grant DROP COLUMN signature;
    """)
