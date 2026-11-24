"""A price for one company: the plan request can carry what the company asked for and what the team offered (CR-148).

Revision ID: commercial_0044
Revises: commercial_0043
Create Date: 2026-11-24

A company can ask for a plan at a different price (`desired_amount`, optional, what it hopes to pay a month). The team answers with an OFFER that belongs to that one company, that one plan and that
one request: an agreed monthly amount, the list price it was measured against, the price book it came from, and a last day. The offer is never an entitlement and moves no plan. It only decides the
amount the company pays when it buys the plan, and the plan still switches only after Stripe confirms that exact payment (CR-062, CR-147).

`offer_state`: PENDING_APPROVAL (a reduction that a second person with the right authority must approve, as with any discount), OPEN (the company may buy at this price until it expires), USED (the plan
was bought and switched on at this price), WITHDRAWN (the team took it back). The columns live on the request's own row, so the company already sees only its own offers (row-level security) and the
operator reads across companies only inside the operator scope, exactly as for the rest of the request.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0044"
down_revision = "commercial_0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE commercial.plan_request
            ADD COLUMN desired_amount       numeric(12, 2) CHECK (desired_amount IS NULL OR desired_amount > 0),
            ADD COLUMN offer_amount         numeric(12, 2) CHECK (offer_amount IS NULL OR offer_amount > 0),
            ADD COLUMN offer_list_amount    numeric(12, 2) CHECK (offer_list_amount IS NULL OR offer_list_amount > 0),
            ADD COLUMN offer_currency       text CHECK (offer_currency IS NULL OR offer_currency ~ '^[A-Z]{3}$'),
            ADD COLUMN offer_price_book_id  uuid,
            ADD COLUMN offer_expires_at     timestamptz,
            ADD COLUMN offered_by           uuid,
            ADD COLUMN offered_at           timestamptz,
            ADD COLUMN offer_approved_by    uuid,
            ADD COLUMN offer_state          text
                CHECK (offer_state IS NULL OR offer_state IN ('PENDING_APPROVAL', 'OPEN', 'USED', 'WITHDRAWN'));

        -- an offer is all or nothing: the amount, what it was measured against, who made it and when it ends go together
        ALTER TABLE commercial.plan_request ADD CONSTRAINT plan_request_offer_whole CHECK (
            (offer_state IS NULL
                AND offer_amount IS NULL AND offer_list_amount IS NULL AND offer_currency IS NULL
                AND offer_price_book_id IS NULL AND offer_expires_at IS NULL AND offered_by IS NULL AND offered_at IS NULL)
            OR
            (offer_state IS NOT NULL
                AND offer_amount IS NOT NULL AND offer_list_amount IS NOT NULL AND offer_currency IS NOT NULL
                AND offer_price_book_id IS NOT NULL AND offer_expires_at IS NOT NULL AND offered_by IS NOT NULL AND offered_at IS NOT NULL)
        );
        -- a price may be lower than the list price it was measured against, never higher
        ALTER TABLE commercial.plan_request ADD CONSTRAINT plan_request_offer_not_above_list CHECK (
            offer_amount IS NULL OR offer_amount <= offer_list_amount
        );
        -- an OPEN or USED offer has been approved by someone: the person with the authority for its reduction
        ALTER TABLE commercial.plan_request ADD CONSTRAINT plan_request_offer_approved CHECK (
            offer_state IS NULL OR offer_state IN ('PENDING_APPROVAL', 'WITHDRAWN') OR offer_approved_by IS NOT NULL
        );
        CREATE INDEX plan_request_open_offer ON commercial.plan_request (tenant_id, plan_code) WHERE offer_state = 'OPEN';
    """)


def downgrade() -> None:
    _run("""
        DROP INDEX IF EXISTS commercial.plan_request_open_offer;
        ALTER TABLE commercial.plan_request
            DROP CONSTRAINT IF EXISTS plan_request_offer_approved,
            DROP CONSTRAINT IF EXISTS plan_request_offer_not_above_list,
            DROP CONSTRAINT IF EXISTS plan_request_offer_whole,
            DROP COLUMN IF EXISTS offer_state,
            DROP COLUMN IF EXISTS offer_approved_by,
            DROP COLUMN IF EXISTS offered_at,
            DROP COLUMN IF EXISTS offered_by,
            DROP COLUMN IF EXISTS offer_expires_at,
            DROP COLUMN IF EXISTS offer_price_book_id,
            DROP COLUMN IF EXISTS offer_currency,
            DROP COLUMN IF EXISTS offer_list_amount,
            DROP COLUMN IF EXISTS offer_amount,
            DROP COLUMN IF EXISTS desired_amount;
    """)
