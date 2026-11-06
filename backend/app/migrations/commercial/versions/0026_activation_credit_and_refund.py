"""A paid activation can be spread over invoices, and can be refunded (CR-062 follow-up).

Revision ID: commercial_0026
Revises: commercial_0025
Create Date: 2026-11-06

- commercial.activation_credit: each time a paid activation is taken off an invoice, how much and which invoice. The platform fee of a part month is prorated by
  the rating run while the company paid a whole month, so the credit may not fit on one invoice: what does not fit carries to the next. Voiding a draft
  invoice releases what it took (the row is marked released, never deleted).
- activation_payment gains the states REFUND_DUE (an operator decided the payment is to be returned) and REFUNDED (Stripe reported the refund), the reason, and the
  time. A payment in either state is never credited to an invoice.
- An operator-plane policy lets the operator list the payments to refund across companies; a company still sees only its own.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0026"
down_revision = "commercial_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE commercial.activation_payment DROP CONSTRAINT activation_payment_status_check;
        ALTER TABLE commercial.activation_payment ADD CONSTRAINT activation_payment_status_check
            CHECK (status IN ('AWAITING', 'PAID', 'CREDITED', 'CANCELLED', 'REFUND_DUE', 'REFUNDED'));
        ALTER TABLE commercial.activation_payment ADD COLUMN refund_reason text CHECK (char_length(refund_reason) BETWEEN 3 AND 500);
        ALTER TABLE commercial.activation_payment ADD COLUMN refund_decided_by uuid;
        ALTER TABLE commercial.activation_payment ADD COLUMN refund_decided_at timestamptz;
        ALTER TABLE commercial.activation_payment ADD COLUMN refunded_at timestamptz;
        CREATE POLICY operator_scope ON commercial.activation_payment
            USING (current_setting('app.commercial_scope', true) = 'operator')
            WITH CHECK (current_setting('app.commercial_scope', true) = 'operator');

        CREATE TABLE commercial.activation_credit (
            order_id    uuid NOT NULL REFERENCES commercial.activation_payment (order_id),
            invoice_id  uuid NOT NULL,
            tenant_id   uuid NOT NULL,
            amount      numeric(18, 6) NOT NULL CHECK (amount > 0),
            created_at  timestamptz NOT NULL DEFAULT now(),
            released_at timestamptz,  -- the draft invoice was voided: this much is available again (the runtime role never deletes)
            PRIMARY KEY (order_id, invoice_id)
        );
        ALTER TABLE commercial.activation_credit ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.activation_credit FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON commercial.activation_credit
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE commercial.activation_credit;
        DROP POLICY operator_scope ON commercial.activation_payment;
        ALTER TABLE commercial.activation_payment DROP COLUMN refunded_at, DROP COLUMN refund_decided_at, DROP COLUMN refund_decided_by, DROP COLUMN refund_reason;
        ALTER TABLE commercial.activation_payment DROP CONSTRAINT activation_payment_status_check;
        ALTER TABLE commercial.activation_payment ADD CONSTRAINT activation_payment_status_check
            CHECK (status IN ('AWAITING', 'PAID', 'CREDITED', 'CANCELLED'));
    """)
