"""API-based agents: an agent whose software proof is a registered API client, not a container image (CR-093).

Revision ID: machine_0010
Revises: machine_0009
Create Date: 2026-11-14

A build is still what a blueprint release comes from and a second person still approves it; `kind` says what its digest names. For an
`API_CLIENT` build the digest is the SHA-256 of a descriptor (the client id, the RFC 7638 thumbprints of its public keys and its audiences),
so changing a key makes a different digest, which is a different build that needs its own approval. `client_descriptor` holds the PUBLIC key
set and nothing secret. `api_client_binding` is the API counterpart of a workload binding: it satisfies the runtime-binding activation gate and
is suspended or revoked by the same kill paths. Tenant-scoped under row-level security.
"""

from zid_platform.migrations import execute_script as _run

revision = "machine_0010"
down_revision = "machine_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE agent.build_artifact ADD COLUMN kind text NOT NULL DEFAULT 'IMAGE' CHECK (kind IN ('IMAGE','API_CLIENT'));
        ALTER TABLE agent.build_artifact ADD COLUMN client_descriptor jsonb;
        ALTER TABLE agent.build_artifact ADD CONSTRAINT build_artifact_descriptor_kind
            CHECK ((kind = 'API_CLIENT') = (client_descriptor IS NOT NULL));

        CREATE TABLE agent.api_client_binding (
            binding_id          uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            agent_principal_id  uuid NOT NULL,
            build_id            uuid NOT NULL REFERENCES agent.build_artifact (build_id),
            client_id           text NOT NULL CHECK (char_length(client_id) BETWEEN 8 AND 80),
            descriptor_digest   text NOT NULL CHECK (descriptor_digest ~ '^sha256:[0-9a-f]{64}$'),
            status              text NOT NULL CHECK (status IN ('ACTIVE','SUSPENDED','REVOKED')),
            security_revision   integer NOT NULL DEFAULT 0 CHECK (security_revision >= 0),
            valid_to            timestamptz NOT NULL,
            created_by          uuid NOT NULL,
            created_at          timestamptz NOT NULL DEFAULT now(),
            revoked_at          timestamptz,
            revoke_reason       text CHECK (char_length(revoke_reason) <= 200)
        );
        CREATE UNIQUE INDEX api_client_binding_one_live ON agent.api_client_binding (tenant_id, agent_principal_id)
            WHERE status IN ('ACTIVE','SUSPENDED');
        CREATE INDEX api_client_binding_digest ON agent.api_client_binding (tenant_id, descriptor_digest);
        ALTER TABLE agent.api_client_binding ENABLE ROW LEVEL SECURITY;
        ALTER TABLE agent.api_client_binding FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON agent.api_client_binding
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE agent.api_client_binding;
        ALTER TABLE agent.build_artifact DROP CONSTRAINT build_artifact_descriptor_kind;
        ALTER TABLE agent.build_artifact DROP COLUMN client_descriptor;
        ALTER TABLE agent.build_artifact DROP COLUMN kind;
    """)
