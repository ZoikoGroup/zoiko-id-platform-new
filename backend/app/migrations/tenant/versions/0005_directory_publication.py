"""What this cell last published to the global locator, per tenant (DEC-G3; G-W6; CR-016).

Revision ID: tenant_0005
Revises: tenant_0004
Create Date: 2026-10-22

directory_publication: the record revision and content hash last accepted by the global plane, so
the worker publishes only changes and every publication carries a strictly increasing revision.
Tenant-scoped under RLS (the worker runs BYPASSRLS).
"""

from zid_platform.migrations import execute_script as _run

revision = "tenant_0005"
down_revision = "tenant_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.directory_publication (
            tenant_id         uuid PRIMARY KEY,
            record_revision   bigint NOT NULL CHECK (record_revision >= 1),
            record_hash       text NOT NULL,
            published_at      timestamptz NOT NULL DEFAULT now(),
            last_error        text
        );
        ALTER TABLE platform.directory_publication ENABLE ROW LEVEL SECURITY;
        ALTER TABLE platform.directory_publication FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON platform.directory_publication
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("DROP TABLE platform.directory_publication;")
