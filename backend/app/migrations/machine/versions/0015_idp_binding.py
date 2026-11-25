"""Outside identity provider bindings, as evidence (ZID-CP-WF07 v2.0 .03; CR-169).

Revision ID: machine_0015
Revises: machine_0014
Create Date: 2026-11-25

`agent.idp_binding` ties one outside provider's issuer and subject to one agent, with the PUBLIC keys the company pinned for that provider. A partial unique index refuses a second live binding of the same issuer and subject, so a
display name or an e-mail can never merge two agents. `agent.idp_proof` remembers each token accepted as proof, by a digest of its id, so one token proves a binding once; nothing here stores a token. Tenant-scoped under RLS.
"""

from zid_platform.migrations import execute_script as _run

revision = "machine_0015"
down_revision = "machine_0014"
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE agent.idp_binding (
            binding_id        uuid PRIMARY KEY,
            tenant_id         uuid NOT NULL,
            agent_principal_id uuid NOT NULL REFERENCES agent.agent_profile (agent_principal_id),
            profile           text NOT NULL CHECK (profile IN ('generic_oidc', 'okta', 'entra')),
            issuer            text NOT NULL,
            subject           text NOT NULL,
            audience          text NOT NULL,
            environment       text NOT NULL DEFAULT 'PRODUCTION' CHECK (environment IN ('SANDBOX', 'PRODUCTION')),
            keys              jsonb NOT NULL,
            algs              text[] NOT NULL,
            keys_fingerprint  text NOT NULL,
            status            text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'REVOKED')),
            external_claims   jsonb NOT NULL DEFAULT '{{}}',
            claims_seen_at    timestamptz,
            last_ok_at        timestamptz,
            last_failure      text,
            created_by        uuid NOT NULL,
            created_at        timestamptz NOT NULL DEFAULT now(),
            revoked_by        uuid,
            revoked_at        timestamptz,
            expires_at        timestamptz,
            CHECK ((status = 'REVOKED') = (revoked_at IS NOT NULL))
        );
        CREATE UNIQUE INDEX idp_binding_one_live ON agent.idp_binding (tenant_id, issuer, subject, environment) WHERE status = 'ACTIVE';
        CREATE INDEX idp_binding_agent ON agent.idp_binding (tenant_id, agent_principal_id);

        CREATE TABLE agent.idp_proof (
            binding_id uuid NOT NULL REFERENCES agent.idp_binding (binding_id),
            tenant_id  uuid NOT NULL,
            jti_digest text NOT NULL,
            accepted_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (binding_id, jti_digest)
        );

        ALTER TABLE agent.idp_binding ENABLE ROW LEVEL SECURITY;
        ALTER TABLE agent.idp_binding FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON agent.idp_binding USING ({TENANT}) WITH CHECK ({TENANT});
        ALTER TABLE agent.idp_proof ENABLE ROW LEVEL SECURITY;
        ALTER TABLE agent.idp_proof FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON agent.idp_proof USING ({TENANT}) WITH CHECK ({TENANT});
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE agent.idp_proof, agent.idp_binding;
    """)
