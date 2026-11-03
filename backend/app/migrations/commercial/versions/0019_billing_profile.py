"""Billing profile: the payer's address, tax identifiers and billing contacts, versioned (ZID-UX-COM-008 B04; slice L-14d, CR-054).

Revision ID: commercial_0019
Revises: commercial_0018
Create Date: 2026-11-03

billing_profile: one current row per organization, replaced only with the version it was based on (a stale edit is a conflict).
billing_profile_history: every version ever saved, who saved it and which fields changed; append-only, so a change of invoice routing can always be shown.
The legal payer's name and tax country are NOT here: they come from the commercial account, and changing them is a reviewed change, never an inline edit.
Both tables are tenant-scoped.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0019"
down_revision = "commercial_0018"
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE commercial.billing_profile (
            tenant_id  uuid PRIMARY KEY,
            version    integer NOT NULL CHECK (version >= 1),
            address    jsonb NOT NULL,
            tax_ids    jsonb NOT NULL,
            contacts   jsonb NOT NULL,
            updated_by uuid NOT NULL,
            updated_at timestamptz NOT NULL
        );
        CREATE TABLE commercial.billing_profile_history (
            tenant_id      uuid NOT NULL,
            version        integer NOT NULL CHECK (version >= 1),
            address        jsonb NOT NULL,
            tax_ids        jsonb NOT NULL,
            contacts       jsonb NOT NULL,
            changed_fields text[] NOT NULL,
            changed_by     uuid NOT NULL,
            changed_at     timestamptz NOT NULL,
            PRIMARY KEY (tenant_id, version)
        );
        CREATE TRIGGER billing_profile_history_append_only BEFORE UPDATE OR DELETE ON commercial.billing_profile_history
            FOR EACH ROW EXECUTE FUNCTION commercial.finance_append_only();
        ALTER TABLE commercial.billing_profile ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.billing_profile FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON commercial.billing_profile USING ({TENANT}) WITH CHECK ({TENANT});
        ALTER TABLE commercial.billing_profile_history ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.billing_profile_history FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON commercial.billing_profile_history USING ({TENANT}) WITH CHECK ({TENANT});
    """)


def downgrade() -> None:
    pass
