"""Tool grants are used once: the gateway remembers every grant it signs (ZID-GAP-001 WP-11; MACHINE-001 §14-15).

Revision ID: machine_0007
Revises: machine_0006
Create Date: 2026-11-10

A grant is a signed statement that one tool call was allowed. A signature alone cannot say "only once", so the gateway
records each grant's nonce (`grant_id`, the token's `jti`) here, and the tool's server redeems it: the row is marked
consumed in one atomic UPDATE, so a second presentation, even in parallel, finds it already used. The row also carries what the grant was
bound to (tool, audience, resource, arguments digest) so redemption can check the server is acting on exactly that.
One row per grant, never deleted. Tenant-scoped under RLS.
"""

from zid_platform.migrations import execute_script as _run

revision = "machine_0007"
down_revision = "machine_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE agent.tool_grant (
            grant_id        uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            agent_principal_id uuid NOT NULL,
            tool_id         text NOT NULL,
            audience        uuid NOT NULL,
            resource        text NOT NULL,
            args_sha256     text NOT NULL CHECK (args_sha256 ~ '^sha256:[0-9a-f]{64}$'),
            decision_id     text NOT NULL,
            issued_at       timestamptz NOT NULL DEFAULT now(),
            expires_at      timestamptz NOT NULL,
            consumed_at     timestamptz,
            consumed_by     uuid,
            CHECK (expires_at > issued_at),
            CHECK ((consumed_at IS NULL) = (consumed_by IS NULL))
        );
        CREATE INDEX tool_grant_agent ON agent.tool_grant (tenant_id, agent_principal_id, issued_at DESC);
        ALTER TABLE agent.tool_grant ENABLE ROW LEVEL SECURITY;
        ALTER TABLE agent.tool_grant FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON agent.tool_grant
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("DROP TABLE agent.tool_grant;")
