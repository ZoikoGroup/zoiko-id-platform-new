"""Identity signals may open review items (CR-101; ZID-GAP-001 v2.0 DTV-20, DTV-21, DTV-22).

Revision ID: screening_0005
Revises: screening_0004
Create Date: 2026-11-16

screening_signal.source also allows 'IDENTITY_SIGNAL': a link group or a shared device found by the identity-signal rules opens the same
review item as a velocity breach, with counts only. Nothing else changes: the table stays append-only and one open item per dedupe key.
"""

from zid_platform.migrations import execute_script as _run

revision = "screening_0005"
down_revision = "screening_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE verify.screening_signal DROP CONSTRAINT screening_signal_source_check;
        ALTER TABLE verify.screening_signal
            ADD CONSTRAINT screening_signal_source_check
            CHECK (source IN ('VELOCITY', 'ADVERSE_MEDIA', 'IDENTITY_SIGNAL'));
    """)


def downgrade() -> None:
    pass
