"""Tenant data deletion record (TENANCY-001 §23; PRIV-001; TEN-25).

Revision ID: onboarding_0002
Revises: onboarding_0001
Create Date: 2026-10-26

tenant_deletion: one row per closed tenant whose data is being deleted. It records who asked, what the
crypto-erasure did (subjects erased, held back by a legal hold, connector secrets destroyed) and the two dates the
platform still owes: when backups holding the data age out, and when the scheduled key destruction completes.
"""

from zid_platform.migrations import execute_script as _run

revision = "onboarding_0002"
down_revision = "onboarding_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.tenant_deletion (
            tenant_id            uuid PRIMARY KEY,
            requested_by         text NOT NULL,
            requested_at         timestamptz NOT NULL,
            status               text NOT NULL CHECK (status IN ('REQUESTED', 'COMPLETE', 'HELD')),
            report               jsonb NOT NULL DEFAULT '{}'::jsonb,
            backup_erasure_due   timestamptz,
            key_destruction_due  timestamptz,
            completed_at         timestamptz
        );
        ALTER TABLE platform.tenant_deletion ENABLE ROW LEVEL SECURITY;
        ALTER TABLE platform.tenant_deletion FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON platform.tenant_deletion
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("DROP TABLE platform.tenant_deletion;")
