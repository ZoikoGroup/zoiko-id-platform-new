"""A company's monthly verification spend budget (ZID-DTV-WF-01 WF01-014; CR-107).

Revision ID: verification_0028
Revises: verification_0027
Create Date: 2026-11-17

One row per company: the most Zoiko may spend on providers for that company's verifications in a calendar month (UTC), in one currency. There is no default and
no placeholder amount: a company with no row has no budget. Spend is read from the vendor cost events (commercial), counted in the budget's own currency only:
no exchange rate is invented. Clearing a budget switches it off (`enabled` false) and keeps the last limit and who set it: the runtime role never deletes rows.
Row-level security by company.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0028"
down_revision = "verification_0027"
branch_labels = None
depends_on = None

RULE = "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE verify.verification_budget (
            tenant_id     uuid PRIMARY KEY,
            monthly_limit numeric(18, 6) NOT NULL CHECK (monthly_limit >= 0),
            currency      text NOT NULL CHECK (currency ~ '^[A-Z]{{3}}$'),
            enabled       boolean NOT NULL DEFAULT true,
            set_by        uuid NOT NULL,
            set_at        timestamptz NOT NULL,
            version       integer NOT NULL DEFAULT 1 CHECK (version >= 1)
        );
        ALTER TABLE verify.verification_budget ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.verification_budget FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.verification_budget USING {RULE} WITH CHECK {RULE};
    """)


def downgrade() -> None:
    _run("DROP TABLE verify.verification_budget;")
