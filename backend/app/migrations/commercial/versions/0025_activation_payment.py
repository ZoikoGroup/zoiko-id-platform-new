"""Pay before the plan switches: an order waits for the first month's platform fee.

Revision ID: commercial_0025
Revises: commercial_0024
Create Date: 2026-11-06

Until now the operator's activation switched a company's plan at once and the platform fee was billed later, on the month-end invoice. A company can now
be asked to pay the first month's fee first (CR-062):
- commercial.sales_order gains the state AWAITING_PAYMENT, between SIGNED and ACTIVATED. The order itself is never rewritten (the order guard still
  forbids it); only its state moves.
- commercial.activation_payment: one row per order that waits. It holds the amount (the same monthly value the commitment approval used), the Stripe
  Checkout session the buyer was sent to, the payment once Stripe confirms it, and the month-end invoice that took it into account.
- A paid activation is shown on that month's invoice as a CREDIT line, so the fee is never billed twice (invoice_line.kind already allows CREDIT).
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0025"
down_revision = "commercial_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE commercial.sales_order DROP CONSTRAINT sales_order_status_check;
        ALTER TABLE commercial.sales_order ADD CONSTRAINT sales_order_status_check
            CHECK (status IN ('SIGNED', 'AWAITING_PAYMENT', 'ACTIVATED', 'SUPERSEDED'));

        CREATE TABLE commercial.activation_payment (
            order_id            uuid PRIMARY KEY REFERENCES commercial.sales_order (order_id),
            tenant_id           uuid NOT NULL,
            currency            text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
            amount              numeric(18, 6) NOT NULL CHECK (amount > 0),
            activated_by        uuid NOT NULL,
            status              text NOT NULL DEFAULT 'AWAITING' CHECK (status IN ('AWAITING', 'PAID', 'CREDITED', 'CANCELLED')),
            session_id          text,
            payment_ref         text UNIQUE,
            created_at          timestamptz NOT NULL DEFAULT now(),
            paid_at             timestamptz,
            credited_invoice_id uuid
        );
        CREATE INDEX activation_payment_tenant ON commercial.activation_payment (tenant_id, status);
        ALTER TABLE commercial.activation_payment ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.activation_payment FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON commercial.activation_payment
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE commercial.activation_payment;
        ALTER TABLE commercial.sales_order DROP CONSTRAINT sales_order_status_check;
        ALTER TABLE commercial.sales_order ADD CONSTRAINT sales_order_status_check
            CHECK (status IN ('SIGNED', 'ACTIVATED', 'SUPERSEDED'));
    """)
