"""API agent credential path: single-use assertion ids (CR-094).

Revision ID: machine_0011
Revises: machine_0010
Create Date: 2026-11-14

A signed-key client proves itself with a short-lived signed assertion carrying a single-use id. The id is burned here before anything else is
decided, so a replay is refused even when the first use was itself refused. Tenant-scoped under row-level security; rows hold no key and no token.
"""

from zid_platform.migrations import execute_script as _run

revision = "machine_0011"
down_revision = "machine_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE agent.api_assertion_jti (
            tenant_id   uuid NOT NULL,
            jti         text NOT NULL CHECK (char_length(jti) BETWEEN 8 AND 128),
            binding_id  uuid NOT NULL,
            expires_at  timestamptz NOT NULL,
            used_at     timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, jti)
        );
        CREATE INDEX api_assertion_jti_expiry ON agent.api_assertion_jti (expires_at);
        ALTER TABLE agent.api_assertion_jti ENABLE ROW LEVEL SECURITY;
        ALTER TABLE agent.api_assertion_jti FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON agent.api_assertion_jti
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("DROP TABLE agent.api_assertion_jti;")
