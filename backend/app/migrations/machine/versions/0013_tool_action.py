"""Tool actions and their receipts: what happened after the gateway signed a grant (ZID-CP-WF07 v2.0 .07, .11; CR-169).

Revision ID: machine_0013
Revises: machine_0012
Create Date: 2026-11-25

`agent.tool_action` is one row per grant, with the same id: the grant says "this call was allowed", the action says what became of it. Its state only moves along the allowed paths, and an UNKNOWN outcome is a state of its own,
never a failure. `agent.tool_action_receipt` is the signed record of each revision: one row per revision, and a trigger refuses any update or delete, so a receipt can be superseded but never altered. Tenant-scoped under RLS.
"""

from zid_platform.migrations import execute_script as _run

revision = "machine_0013"
down_revision = "machine_0012"
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE agent.tool_action (
            action_id          uuid PRIMARY KEY,
            tenant_id          uuid NOT NULL,
            agent_principal_id uuid NOT NULL,
            tool_id            text NOT NULL,
            audience           uuid NOT NULL,
            resource           text NOT NULL,
            args_sha256        text NOT NULL CHECK (args_sha256 ~ '^sha256:[0-9a-f]{{64}}$'),
            decision_id        text NOT NULL,
            chain_commitment   text NOT NULL,
            agent_chain        text[] NOT NULL,
            mandate_id         uuid,
            mandate_hash       text,
            reservation_id     uuid,
            state              text NOT NULL DEFAULT 'AUTHORIZED' CHECK (state IN ('AUTHORIZED', 'DISPATCHED', 'EXECUTED', 'FAILED', 'UNKNOWN', 'COMPENSATED')),
            revision           integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
            downstream_ref     text CHECK (downstream_ref IS NULL OR char_length(downstream_ref) <= 128),
            reported_by        uuid,
            reported_at        timestamptz,
            created_at         timestamptz NOT NULL DEFAULT now(),
            updated_at         timestamptz NOT NULL DEFAULT now(),
            CHECK (reservation_id IS NULL OR mandate_id IS NOT NULL)
        );
        CREATE INDEX tool_action_agent ON agent.tool_action (tenant_id, agent_principal_id, created_at DESC);
        CREATE INDEX tool_action_state ON agent.tool_action (tenant_id, state, updated_at DESC);

        CREATE TABLE agent.tool_action_receipt (
            receipt_id uuid PRIMARY KEY,
            action_id  uuid NOT NULL REFERENCES agent.tool_action (action_id),
            tenant_id  uuid NOT NULL,
            revision   integer NOT NULL CHECK (revision >= 1),
            state      text NOT NULL,
            payload    jsonb NOT NULL,
            digest     text NOT NULL CHECK (digest ~ '^sha256:[0-9a-f]{{64}}$'),
            signature  text NOT NULL,
            key_id     text,
            issued_at  timestamptz NOT NULL,
            UNIQUE (action_id, revision)
        );
        CREATE FUNCTION agent.tool_action_receipt_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'receipts are append-only: a change is a new revision' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER tool_action_receipt_no_change BEFORE UPDATE OR DELETE ON agent.tool_action_receipt FOR EACH ROW EXECUTE FUNCTION agent.tool_action_receipt_immutable();

        ALTER TABLE agent.tool_action ENABLE ROW LEVEL SECURITY;
        ALTER TABLE agent.tool_action FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON agent.tool_action USING ({TENANT}) WITH CHECK ({TENANT});
        ALTER TABLE agent.tool_action_receipt ENABLE ROW LEVEL SECURITY;
        ALTER TABLE agent.tool_action_receipt FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON agent.tool_action_receipt USING ({TENANT}) WITH CHECK ({TENANT});
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE agent.tool_action_receipt, agent.tool_action;
        DROP FUNCTION agent.tool_action_receipt_immutable();
    """)
