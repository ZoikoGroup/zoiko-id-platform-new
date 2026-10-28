"""Processor events awaiting a person's decision (ZID-COM-001 §11; CR-041).

Revision ID: commercial_0014
Revises: commercial_0013
Create Date: 2026-10-28

A refund, a dispute or an unmatched payment needs an operator. The event row now keeps what the operator needs to act
(the invoice, the amount and currency) and records who resolved it and how. The payload is still not stored.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0014"
down_revision = "commercial_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE commercial.stripe_event
            ADD COLUMN invoice_id  uuid,
            ADD COLUMN amount      numeric(18, 6),
            ADD COLUMN currency    text,
            ADD COLUMN resolved_by uuid,
            ADD COLUMN resolved_at timestamptz,
            ADD COLUMN resolution  text CHECK (resolution IS NULL OR char_length(resolution) <= 500),
            ADD CONSTRAINT stripe_event_resolved_whole
                CHECK ((resolved_by IS NULL) = (resolved_at IS NULL) AND (resolved_at IS NULL) = (resolution IS NULL));
    """)


def downgrade() -> None:
    _run("""
        ALTER TABLE commercial.stripe_event
            DROP CONSTRAINT stripe_event_resolved_whole,
            DROP COLUMN resolution, DROP COLUMN resolved_at, DROP COLUMN resolved_by,
            DROP COLUMN currency, DROP COLUMN amount, DROP COLUMN invoice_id;
    """)
