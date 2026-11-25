"""The operator-set price of a Verify API check (ZID-EXT-IDV-001 section 12, WF-15; decision D12; CR-161).

Revision ID: verify_api_0009
Revises: verify_api_0008
Create Date: 2026-11-25

`api_price` is one price for one usage fact (a production session started, or a check completed), in one currency, from the first day of one month. An operator proposes it and a DIFFERENT operator approves it; an
approved price is never edited or deleted, a later one supersedes it from its own month. The list is the same for every company (it is the product's price, not a company's), so it is not tenant-scoped: anyone may
READ it, only the operator scope may write. It prices USAGE FACTS for the usage screen. It is not an invoice and does not touch the commercial price book or rating: invoicing a verification is a separate step
the commercial estate owns.
"""

from zid_platform.migrations import execute_script as _run

revision = "verify_api_0009"
down_revision = "verify_api_0008"
branch_labels = None
depends_on = None

OPERATOR = "current_setting('app.verify_api_scope', true) = 'operator'"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE verify.api_price (
            price_id        uuid PRIMARY KEY,
            meter           text NOT NULL CHECK (meter IN ('VERIFY_SESSION_START', 'VERIFY_CHECK_COMPLETE')),
            currency        text NOT NULL CHECK (currency ~ '^[A-Z]{{3}}$'),
            unit_rate       numeric(18, 6) NOT NULL CHECK (unit_rate >= 0),
            effective_from  date NOT NULL CHECK (extract(day FROM effective_from) = 1),
            status          text NOT NULL DEFAULT 'PROPOSED' CHECK (status IN ('PROPOSED', 'APPROVED', 'REJECTED')),
            proposed_by     uuid NOT NULL,
            decided_by      uuid,
            note            text,
            created_at      timestamptz NOT NULL DEFAULT now(),
            decided_at      timestamptz,
            CHECK ((status = 'PROPOSED') = (decided_by IS NULL)),
            CHECK (decided_by IS NULL OR decided_by <> proposed_by)
        );
        -- one approved price for a meter, currency and month; a correction is a new price from a later month
        CREATE UNIQUE INDEX api_price_one_approved ON verify.api_price (meter, currency, effective_from) WHERE status = 'APPROVED';
        CREATE INDEX api_price_lookup ON verify.api_price (meter, currency, effective_from DESC) WHERE status = 'APPROVED';
        ALTER TABLE verify.api_price ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.api_price FORCE ROW LEVEL SECURITY;
        CREATE POLICY api_price_read ON verify.api_price FOR SELECT USING (true);
        CREATE POLICY api_price_operator ON verify.api_price FOR ALL USING ({OPERATOR}) WITH CHECK ({OPERATOR});
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.api_price;
    """)
