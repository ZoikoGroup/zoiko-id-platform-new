"""A plan request carries the company's name (CR-032 follow-up).

Revision ID: commercial_0012
Revises: commercial_0011
Create Date: 2026-10-27

The operator plane answers requests from every company but cannot read another company's tenant row (row-level
security), so the list showed raw ids. The company's own request now records its display name when it is made.
Requests made before this change are filled in from the tenant table when it exists.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0012"
down_revision = "commercial_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE commercial.plan_request
            ADD COLUMN company_name text CHECK (company_name IS NULL OR char_length(company_name) <= 300);
        DO $$
        BEGIN
            IF to_regclass('platform.tenant') IS NOT NULL THEN
                UPDATE commercial.plan_request p SET company_name = t.display_name
                  FROM platform.tenant t WHERE t.tenant_id = p.tenant_id AND p.company_name IS NULL;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    _run("ALTER TABLE commercial.plan_request DROP COLUMN company_name;")
