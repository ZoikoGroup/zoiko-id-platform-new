"""Let the operator plane answer enterprise service requests across companies.

Revision ID: commercial_0023
Revises: commercial_0022
Create Date: 2026-11-05

The same second policy plan requests use: it opens only inside a transaction the operator surface has put into operator scope (`app.commercial_scope`),
so an operator reads and moves every company's requests while a tenant still sees only its own. `moved_by` and `moved_at` record who moved a request last.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0023"
down_revision = "commercial_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE commercial.service_request ADD COLUMN moved_by uuid, ADD COLUMN moved_at timestamptz;
        CREATE POLICY operator_scope ON commercial.service_request
            USING (current_setting('app.commercial_scope', true) = 'operator')
            WITH CHECK (current_setting('app.commercial_scope', true) = 'operator');
    """)


def downgrade() -> None:
    _run("""
        DROP POLICY operator_scope ON commercial.service_request;
        ALTER TABLE commercial.service_request DROP COLUMN moved_by, DROP COLUMN moved_at;
    """)
