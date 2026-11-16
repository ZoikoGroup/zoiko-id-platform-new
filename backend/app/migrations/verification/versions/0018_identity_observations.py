"""Identity observations: which hashed device was seen with which person (ZID-GAP-001 v2.0 DTV-21, DTV-22; CR-102).

Revision ID: verification_0018
Revises: verification_0017
Create Date: 2026-11-16

identity_observation: one row for each (person, kind, hashed identifier) a company has seen, so the link-graph rules can find people connected by a
shared device. The identifier is a keyed HMAC digest, never the device id, a phone number or an email. Rows older than 90 days are removed by the
worker (a PROPOSED retention: Privacy owns the number). Row-level security by company.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0018"
down_revision = "verification_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.identity_observation (
            tenant_id    uuid NOT NULL,
            subject      uuid NOT NULL,
            kind         text NOT NULL CHECK (kind IN ('PHONE', 'EMAIL', 'DEVICE')),
            identifier   text NOT NULL CHECK (identifier ~ '^[0-9a-f]{64}$'),
            first_seen   timestamptz NOT NULL,
            last_seen    timestamptz NOT NULL,
            PRIMARY KEY (tenant_id, subject, kind, identifier)
        );
        CREATE INDEX identity_observation_recent ON verify.identity_observation (tenant_id, last_seen DESC);
        ALTER TABLE verify.identity_observation ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.identity_observation FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.identity_observation
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("DROP TABLE verify.identity_observation;")
