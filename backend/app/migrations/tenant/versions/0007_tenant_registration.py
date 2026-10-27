"""What a company declared when it registered (ZID-OVERLAY-001 §4; ORG-001 §19 step 1; PRIV-001
notice at collection; TENANCY-001 §14 placement; CR-015).

Revision ID: tenant_0007
Revises: tenant_0006
Create Date: 2026-10-22

tenant_registration: one row per self-registered tenant, written in the sign-up transaction.
- the declared register and identifier (and optional LEI) the company verification step submits;
  public register identifiers, not personal data, and never trusted until the register confirms
  them (the organization gate re-reads the register);
- the privacy-notice version the registrant acknowledged and when;
- the data location (home cell) the registrant chose; the tenant's home cell is the same value,
  fixed from then on except through a governed migration.
"""

from zid_platform.migrations import execute_script as _run

revision = "tenant_0007"
down_revision = "tenant_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.tenant_registration (
            tenant_id            uuid PRIMARY KEY REFERENCES platform.tenant (tenant_id),
            register_namespace   text NOT NULL CHECK (register_namespace IN ('GB-COH')),
            register_identifier  text NOT NULL CHECK (register_identifier ~ '^[A-Z0-9]{6,20}$'),
            lei                  text CHECK (lei IS NULL OR lei ~ '^[A-Z0-9]{18}[0-9]{2}$'),
            notice_version       text NOT NULL CHECK (length(notice_version) BETWEEN 1 AND 40),
            notice_accepted_at   timestamptz NOT NULL,
            home_cell_id         text NOT NULL CHECK (home_cell_id ~ '^[a-z0-9-]{1,40}$'),
            declared_at          timestamptz NOT NULL DEFAULT now()
        );
        ALTER TABLE platform.tenant_registration ENABLE ROW LEVEL SECURITY;
        ALTER TABLE platform.tenant_registration FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON platform.tenant_registration
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("DROP TABLE platform.tenant_registration;")
