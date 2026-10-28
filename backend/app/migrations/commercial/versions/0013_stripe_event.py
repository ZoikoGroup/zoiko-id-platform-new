"""Processor webhook events received (ZID-COM-001 §11; CR-040).

Revision ID: commercial_0013
Revises: commercial_0012
Create Date: 2026-10-28

One row per Stripe event id, so a redelivered event is recognised and never applied twice. The row keeps the event
type, the customer reference and the outcome only; the payload is not stored. No tenant column and no row-level
security: an event arrives before any tenant is known and is matched to one through `payment_customer`.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0013"
down_revision = "commercial_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE commercial.stripe_event (
            event_id     text PRIMARY KEY CHECK (event_id ~ '^evt_[A-Za-z0-9_]{6,100}$'),
            event_type   text NOT NULL CHECK (char_length(event_type) <= 120),
            customer_ref text,
            tenant_id    uuid,
            outcome      text NOT NULL CHECK (outcome IN
                ('APPLIED', 'IGNORED', 'NEEDS_REVIEW', 'UNMATCHED')),
            detail       text CHECK (detail IS NULL OR char_length(detail) <= 300),
            received_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX stripe_event_review ON commercial.stripe_event (received_at)
            WHERE outcome IN ('NEEDS_REVIEW', 'UNMATCHED');
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE commercial.stripe_event;
    """)
