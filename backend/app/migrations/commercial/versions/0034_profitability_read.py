"""Read-only operator access to rated usage, for profitability and run-rate (ZID-COM-WF-2026-11 CW-13).

Revision ID: commercial_0034
Revises: commercial_0033
Create Date: 2026-11-13

Profitability compares what was rated to companies with what providers cost, across every company, so the operator plane needs to read `rating_run` and `rated_usage`
across tenants. These are SELECT-only policies on the operator scope: the operator still cannot write a rated line, and a company still sees only its own.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0034"
down_revision = "commercial_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE POLICY operator_read ON commercial.rating_run FOR SELECT
            USING (current_setting('app.commercial_scope', true) = 'operator');
        CREATE POLICY operator_read ON commercial.rated_usage FOR SELECT
            USING (current_setting('app.commercial_scope', true) = 'operator');
    """)


def downgrade() -> None:
    _run("""
        DROP POLICY operator_read ON commercial.rated_usage;
        DROP POLICY operator_read ON commercial.rating_run;
    """)
