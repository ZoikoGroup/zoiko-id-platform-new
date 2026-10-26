"""Payment processor customer references (ZID-COM-001 §11; CR-027).

Revision ID: commercial_0010
Revises: commercial_0009
Create Date: 2026-10-25

`payment_customer` maps a commercial account to its customer id at the payment processor (Stripe). It holds a
reference only: the platform never stores a card number, expiry or CVC. Operator-plane record like `account`.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0010"
down_revision = "commercial_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE commercial.payment_customer (
            account_id   uuid PRIMARY KEY REFERENCES commercial.account (account_id),
            provider     text NOT NULL CHECK (provider IN ('stripe')),
            customer_ref text NOT NULL CHECK (customer_ref ~ '^cus_[A-Za-z0-9]{6,64}$'),
            set_by       uuid NOT NULL,
            set_at       timestamptz NOT NULL DEFAULT now()
        );
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE commercial.payment_customer;
    """)
