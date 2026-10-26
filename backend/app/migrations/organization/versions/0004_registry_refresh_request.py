"""Requested re-checks of a registry fact (ZID-PRIV-001 rectification; PRIV-16).

Revision ID: organization_0004
Revises: organization_0003
Create Date: 2026-10-26

registry_refresh_request: a person disputes a fact the company register supplied. The fact is never edited here: it is
re-read from the register, and the new reading is appended to registry_observation as the superseding record. This
table is only the request to look again and what came of it.
"""

from zid_platform.migrations import execute_script as _run

revision = "organization_0004"
down_revision = "organization_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE org.registry_refresh_request (
            request_id        uuid PRIMARY KEY,
            tenant_id         uuid NOT NULL,
            organization_id   uuid NOT NULL REFERENCES org.organization (organization_id),
            reason_ref        text NOT NULL CHECK (length(reason_ref) BETWEEN 1 AND 128),
            requested_at      timestamptz NOT NULL,
            status            text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'DONE', 'FAILED')),
            outcome           text,
            completed_at      timestamptz
        );
        CREATE INDEX registry_refresh_request_open ON org.registry_refresh_request (organization_id)
            WHERE status = 'OPEN';
        ALTER TABLE org.registry_refresh_request ENABLE ROW LEVEL SECURITY;
        ALTER TABLE org.registry_refresh_request FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON org.registry_refresh_request
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("DROP TABLE org.registry_refresh_request;")
