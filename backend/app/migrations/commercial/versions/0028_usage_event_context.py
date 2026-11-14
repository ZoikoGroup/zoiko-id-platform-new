"""The usage-event contract's extra fields (ZID-COM-WF-2026-11 v3.0 section 04.1, CW-04).

Revision ID: commercial_0028
Revises: commercial_0027
Create Date: 2026-11-13

A billable event can now say who the bill-to payer is (when that is not the tenant), where the call came from (API, SDK, console or a partner, and which
partner), what the operation was and whether it is chargeable, how final it is, the market policy it was judged under, and the supplier rate version behind
its cost. Every column is nullable: NULL means "not stated", never a default of "tenant", "final" or "chargeable". The columns are NOT part of `row_hash`, so
the hash totals of frozen meter windows do not change. Rows stay insert-only.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0028"
down_revision = "commercial_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE commercial.billable_event
            ADD COLUMN legal_payer_id uuid,
            ADD COLUMN origin text CHECK (origin IN ('API', 'SDK', 'CONSOLE', 'PARTNER')),
            ADD COLUMN partner_id uuid,
            ADD COLUMN transaction_type text CHECK (transaction_type IN
                ('SUBMITTED', 'ACCEPTED', 'PROVIDER_EXECUTED', 'DELIVERED', 'RECHECKED', 'VOIDED')),
            ADD COLUMN billable_result text CHECK (billable_result IN ('CHARGEABLE', 'NO_CHARGE')),
            ADD COLUMN finality text CHECK (finality IN ('PENDING', 'PROVISIONAL')),
            ADD COLUMN market_policy text CHECK (market_policy IS NULL OR char_length(market_policy) BETWEEN 1 AND 80),
            ADD COLUMN supplier_rate_version text CHECK (supplier_rate_version IS NULL OR char_length(supplier_rate_version) BETWEEN 1 AND 80),
            ADD CONSTRAINT billable_event_partner_origin CHECK ((origin = 'PARTNER') = (partner_id IS NOT NULL));
    """)


def downgrade() -> None:
    _run("""
        ALTER TABLE commercial.billable_event
            DROP CONSTRAINT billable_event_partner_origin,
            DROP COLUMN legal_payer_id, DROP COLUMN origin, DROP COLUMN partner_id, DROP COLUMN transaction_type,
            DROP COLUMN billable_result, DROP COLUMN finality, DROP COLUMN market_policy, DROP COLUMN supplier_rate_version;
    """)
