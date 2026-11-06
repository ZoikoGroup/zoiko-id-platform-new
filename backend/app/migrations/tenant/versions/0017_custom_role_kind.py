"""Custom roles that add: read and prepare-only extensions to Operator or Viewer.

Revision ID: tenant_0017
Revises: tenant_0016
Create Date: 2026-11-05

A NARROW custom role (the original kind) keeps a person to part of their base role. An EXTEND custom role adds actions the base role lacks, but only reads
and "prepare or request" actions, which still need an independent approval to take effect, and never an approval, an administrative action or a reserved
one. The matrix still decides every other condition (membership, session, tenant state, separation of duties).
"""

from zid_platform.migrations import execute_script as _run

revision = "tenant_0017"
down_revision = "tenant_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE platform.custom_role
            ADD COLUMN kind text NOT NULL DEFAULT 'NARROW' CHECK (kind IN ('NARROW', 'EXTEND'));
    """)


def downgrade() -> None:
    _run("ALTER TABLE platform.custom_role DROP COLUMN kind;")
