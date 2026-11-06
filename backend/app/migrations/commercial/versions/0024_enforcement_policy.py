"""Per-company plan enforcement: switch plan limits on for one company before they are on everywhere.

Revision ID: commercial_0024
Revises: commercial_0023
Create Date: 2026-11-05

The deployment default (`commercial_enforcement`) is `shadow`: a plan gate records what it would refuse and lets the request through. Turning it to `enforce`
for the whole deployment at once is a big step, so the operator can set one company to ENFORCE (a pilot, or a company whose contract is signed) while the
rest stay in shadow, and set it back. The deployment default can only ever be made stricter by this: a company set to SHADOW under a deployment that
enforces is still enforced.
- commercial.enforcement_policy: one row per company; the operator plane writes it through `operator_scope`, the company can only read its own row.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0024"
down_revision = "commercial_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE commercial.enforcement_policy (
            tenant_id uuid PRIMARY KEY,
            mode      text NOT NULL CHECK (mode IN ('SHADOW', 'ENFORCE')),
            reason    text NOT NULL CHECK (char_length(reason) BETWEEN 3 AND 500),
            set_by    uuid NOT NULL,
            set_at    timestamptz NOT NULL DEFAULT now()
        );
        ALTER TABLE commercial.enforcement_policy ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.enforcement_policy FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_read ON commercial.enforcement_policy FOR SELECT
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        CREATE POLICY operator_scope ON commercial.enforcement_policy
            USING (current_setting('app.commercial_scope', true) = 'operator')
            WITH CHECK (current_setting('app.commercial_scope', true) = 'operator');
    """)


def downgrade() -> None:
    _run("DROP TABLE commercial.enforcement_policy;")
