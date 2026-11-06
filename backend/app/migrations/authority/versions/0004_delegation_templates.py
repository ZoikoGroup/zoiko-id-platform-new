"""Delegation workflow templates: a company's reusable shape for the authority it hands out.

Revision ID: authority_0004
Revises: authority_0003
Create Date: 2026-11-05

Plan feature advanced.advanced-delegation-workflow-templates. A template fixes what a delegation may contain (actions, resources, a value limit, how long it
lasts, whether the grantee may pass it on and how far). Applying one issues an ordinary authority grant through the same checks as any other, so a template
can never carry more than the person applying it already holds.
"""

from zid_platform.migrations import execute_script as _run

revision = "authority_0004"
down_revision = "authority_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE authz.delegation_template (
            template_id       uuid PRIMARY KEY,
            tenant_id         uuid NOT NULL,
            name              text NOT NULL CHECK (char_length(name) BETWEEN 2 AND 80),
            description       text NOT NULL DEFAULT '' CHECK (char_length(description) <= 300),
            actions           text[] NOT NULL CHECK (cardinality(actions) >= 1),
            resources         text[] NOT NULL CHECK (cardinality(resources) >= 1),
            purposes          text[],
            value_amount      numeric CHECK (value_amount IS NULL OR value_amount >= 0),
            value_currency    text CHECK (value_currency IS NULL OR value_currency ~ '^[A-Z]{3}$'),
            max_days          integer NOT NULL CHECK (max_days BETWEEN 1 AND 365),
            delegation_allowed boolean NOT NULL DEFAULT false,
            max_depth         integer NOT NULL DEFAULT 0 CHECK (max_depth BETWEEN 0 AND 16),
            min_assurance     integer NOT NULL DEFAULT 0 CHECK (min_assurance BETWEEN 0 AND 3),
            risk_class        integer NOT NULL DEFAULT 3 CHECK (risk_class BETWEEN 0 AND 3),
            status            text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'RETIRED')),
            created_by        uuid NOT NULL,
            created_at        timestamptz NOT NULL DEFAULT now(),
            retired_at        timestamptz,
            CHECK ((value_amount IS NULL) = (value_currency IS NULL))
        );
        CREATE UNIQUE INDEX delegation_template_name ON authz.delegation_template (tenant_id, lower(name)) WHERE status = 'ACTIVE';
        ALTER TABLE authz.delegation_template ENABLE ROW LEVEL SECURITY;
        ALTER TABLE authz.delegation_template FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON authz.delegation_template
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("DROP TABLE authz.delegation_template;")
