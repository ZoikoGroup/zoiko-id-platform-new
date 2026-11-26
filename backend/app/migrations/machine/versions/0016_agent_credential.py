"""Agent credentials and their presentations (ZID-CP-WF07 v2.0 .09; CR-169).

Revision ID: machine_0016
Revises: machine_0015
Create Date: 2026-11-25

`agent.agent_credential` is one credential Zoiko iD issued for one agent, bound to that agent's public key. Its status is the live answer a verifier asks for: ACTIVE until it is revoked, and revoking never un-revokes. The issued
text is kept, because it is signed and not secret. `agent.credential_presentation` remembers each presentation accepted as proof of key possession, by a digest of its id, so one presentation proves possession once. Tenant-scoped under RLS.
"""

from zid_platform.migrations import execute_script as _run

revision = "machine_0016"
down_revision = "machine_0015"
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE agent.agent_credential (
            credential_id      uuid PRIMARY KEY,
            tenant_id          uuid NOT NULL,
            agent_principal_id uuid NOT NULL REFERENCES agent.agent_profile (agent_principal_id),
            jkt                text NOT NULL,
            public_key         jsonb NOT NULL,
            audience           text,
            mandate_id         uuid,
            mandate_hash       text,
            evidence_class     text NOT NULL CHECK (evidence_class IN ('E1', 'E2', 'E3')),
            token              text NOT NULL,
            status             text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'REVOKED')),
            issued_by          uuid NOT NULL,
            issued_at          timestamptz NOT NULL DEFAULT now(),
            expires_at         timestamptz NOT NULL,
            revoked_by         uuid,
            revoked_at         timestamptz,
            CHECK ((status = 'REVOKED') = (revoked_at IS NOT NULL)),
            CHECK (expires_at > issued_at)
        );
        CREATE INDEX agent_credential_agent ON agent.agent_credential (tenant_id, agent_principal_id, issued_at DESC);

        CREATE TABLE agent.credential_presentation (
            credential_id uuid NOT NULL REFERENCES agent.agent_credential (credential_id),
            tenant_id     uuid NOT NULL,
            jti_digest    text NOT NULL,
            relying_party text NOT NULL,
            accepted_at   timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (credential_id, jti_digest)
        );

        CREATE FUNCTION agent.agent_credential_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'a credential is never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status = 'REVOKED' AND NEW.status <> 'REVOKED' THEN
                RAISE EXCEPTION 'a revoked credential never returns' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.token <> OLD.token OR NEW.jkt <> OLD.jkt OR NEW.agent_principal_id <> OLD.agent_principal_id OR NEW.expires_at <> OLD.expires_at OR NEW.mandate_hash IS DISTINCT FROM OLD.mandate_hash THEN
                RAISE EXCEPTION 'an issued credential does not change' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER agent_credential_guard BEFORE UPDATE OR DELETE ON agent.agent_credential FOR EACH ROW EXECUTE FUNCTION agent.agent_credential_guard();

        ALTER TABLE agent.agent_credential ENABLE ROW LEVEL SECURITY;
        ALTER TABLE agent.agent_credential FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON agent.agent_credential USING ({TENANT}) WITH CHECK ({TENANT});
        ALTER TABLE agent.credential_presentation ENABLE ROW LEVEL SECURITY;
        ALTER TABLE agent.credential_presentation FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON agent.credential_presentation USING ({TENANT}) WITH CHECK ({TENANT});
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE agent.credential_presentation, agent.agent_credential;
        DROP FUNCTION agent.agent_credential_guard();
    """)
