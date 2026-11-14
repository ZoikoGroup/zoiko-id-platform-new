"""The setup's own opt-in to the optional AI explanation (ZID-AG-WF-001 sections 06, 09 step 8; CR-091).

Revision ID: access_readiness_0002
Revises: access_readiness_0001
Create Date: 2026-11-14

Off for every existing and new setup. The flag is part of the scope digest an approval signs ("model flag"), so turning it on sends
the setup back to a draft that needs a new dry run and a new approval. The explanation itself is never stored here: a run keeps it
inside its own summary, next to the deterministic result it explains.
"""

from zid_platform.migrations import execute_script as _run

revision = "access_readiness_0002"
down_revision = "access_readiness_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE agent.access_review_setup ADD COLUMN ai_explanation boolean NOT NULL DEFAULT false;
    """)


def downgrade() -> None:
    _run("""
        ALTER TABLE agent.access_review_setup DROP COLUMN ai_explanation;
    """)
