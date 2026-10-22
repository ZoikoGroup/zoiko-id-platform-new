"""Enterprise domain verification (ZID-OVERLAY-001 §5, §24; OVL-15; G-W2; CR-015).

Revision ID: tenant_0003
Revises: tenant_0002
Create Date: 2026-10-22

tenant_domain: one challenge per (tenant, domain, method) attempt. The challenge token is public by
design (it is published in DNS or on the web), never a secret. A domain is VERIFIED in at most one
tenant of the cell at a time (`tenant_domain_verified_once`, enforced across tenants regardless of
RLS): a second claimant gets DOMAIN_ALREADY_CLAIMED and the explicit administrative model decides.
"""

from zid_platform.migrations import execute_script as _run

revision = "tenant_0003"
down_revision = "tenant_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.tenant_domain (
            domain_id          uuid PRIMARY KEY,
            tenant_id          uuid NOT NULL REFERENCES platform.tenant (tenant_id),
            domain             text NOT NULL CHECK (domain ~ '^[a-z0-9.-]{3,253}$'),
            method             text NOT NULL CHECK (method IN ('DNS_TXT', 'HTTPS_WELL_KNOWN')),
            challenge_token    text NOT NULL CHECK (length(challenge_token) >= 24),
            status             text NOT NULL DEFAULT 'PENDING'
                               CHECK (status IN ('PENDING', 'VERIFIED', 'STALE', 'EXPIRED', 'REVOKED')),
            created_by         uuid NOT NULL,
            created_at         timestamptz NOT NULL DEFAULT now(),
            expires_at         timestamptz NOT NULL,
            verified_at        timestamptz,
            last_checked_at    timestamptz,
            reverify_due_at    timestamptz,
            last_error         text,
            CHECK ((status = 'VERIFIED') <= (verified_at IS NOT NULL))
        );
        CREATE UNIQUE INDEX tenant_domain_verified_once ON platform.tenant_domain (domain)
            WHERE status = 'VERIFIED';
        CREATE INDEX tenant_domain_reverify ON platform.tenant_domain (reverify_due_at)
            WHERE status = 'VERIFIED';
        ALTER TABLE platform.tenant_domain ENABLE ROW LEVEL SECURITY;
        ALTER TABLE platform.tenant_domain FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON platform.tenant_domain
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("DROP TABLE platform.tenant_domain;")
