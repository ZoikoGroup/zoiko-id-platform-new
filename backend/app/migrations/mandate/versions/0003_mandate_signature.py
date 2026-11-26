"""Who confirmed the exact terms of a mandate (ZID-CP-WF07 v2.0 .04; CR-169).

Revision ID: mandate_0003
Revises: mandate_0002
Create Date: 2026-11-25

One row per confirmation of one canonical hash by one person in one role. The row carries the platform's signature over that confirmation and how strongly the person had just signed in. A trigger refuses any change or
deletion, and a database check refuses the same person confirming one mandate in both roles. Tenant-scoped under RLS.
"""

from zid_platform.migrations import execute_script as _run

revision = "mandate_0003"
down_revision = "mandate_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE agent.mandate_signature (
            signature_id        uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            mandate_id          uuid NOT NULL,
            role                text NOT NULL CHECK (role IN ('GRANTOR', 'APPROVER')),
            canonical_hash      text NOT NULL CHECK (canonical_hash ~ '^sha256:[0-9a-f]{64}$'),
            signer_principal_id uuid NOT NULL,
            assurance_level     integer NOT NULL,
            auth_time           timestamptz,
            signature           text NOT NULL,
            key_id              text,
            signed_at           timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (tenant_id, mandate_id) REFERENCES agent.mandate (tenant_id, mandate_id),
            UNIQUE (mandate_id, role, canonical_hash)
        );
        CREATE UNIQUE INDEX mandate_signature_one_role_per_person ON agent.mandate_signature (mandate_id, canonical_hash, signer_principal_id);
        CREATE FUNCTION agent.mandate_signature_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'a mandate confirmation is append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER mandate_signature_no_change BEFORE UPDATE OR DELETE ON agent.mandate_signature FOR EACH ROW EXECUTE FUNCTION agent.mandate_signature_immutable();
        ALTER TABLE agent.mandate_signature ENABLE ROW LEVEL SECURITY;
        ALTER TABLE agent.mandate_signature FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON agent.mandate_signature
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE agent.mandate_signature;
        DROP FUNCTION agent.mandate_signature_immutable();
    """)
