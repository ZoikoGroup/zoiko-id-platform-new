"""Stops by scope, with an epoch, and closed by someone else (ZID-CP-WF07 v2.0 .10; A07-08; CR-169).

Revision ID: machine_0014
Revises: machine_0013
Create Date: 2026-11-25

One row per stop: what was stopped (an agent, a sponsor's agents, a blueprint's agents, the whole company), why, by whom, which agents it reached and where it is enforced. `epoch` counts the company's stops and never repeats. A stop is
CLOSED once, by a DIFFERENT person than the one who made it, with a note: that is the "original cause closed" the agents stopped by it wait for before anyone may reinstate them. A trigger lets nothing change but that closing and refuses
deletion. Tenant-scoped under RLS.
"""

from zid_platform.migrations import execute_script as _run

revision = "machine_0014"
down_revision = "machine_0013"
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE agent.revocation_event (
            event_id       uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            epoch          integer NOT NULL CHECK (epoch >= 1),
            scope          text NOT NULL CHECK (scope IN ('AGENT', 'SPONSOR', 'BLUEPRINT', 'ISSUER', 'TENANT')),
            subject_ref    text NOT NULL,
            reason         text NOT NULL CHECK (char_length(reason) BETWEEN 3 AND 300),
            actor          uuid NOT NULL,
            targets        jsonb NOT NULL,
            enforcement    jsonb NOT NULL,
            freeze_case_id uuid,
            effective_at   timestamptz NOT NULL DEFAULT now(),
            closed_by      uuid,
            closed_at      timestamptz,
            close_note     text,
            UNIQUE (tenant_id, epoch),
            CHECK ((closed_at IS NULL) = (closed_by IS NULL)),
            CHECK (closed_by IS NULL OR closed_by <> actor)
        );
        CREATE INDEX revocation_event_open ON agent.revocation_event (tenant_id, effective_at DESC) WHERE closed_at IS NULL;
        CREATE FUNCTION agent.revocation_event_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'a stop is never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.closed_at IS NOT NULL THEN
                RAISE EXCEPTION 'a closed stop does not change' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.event_id <> OLD.event_id OR NEW.tenant_id <> OLD.tenant_id OR NEW.epoch <> OLD.epoch OR NEW.scope <> OLD.scope OR NEW.subject_ref <> OLD.subject_ref
               OR NEW.reason <> OLD.reason OR NEW.actor <> OLD.actor OR NEW.targets <> OLD.targets OR NEW.enforcement <> OLD.enforcement OR NEW.effective_at <> OLD.effective_at THEN
                RAISE EXCEPTION 'only the closing of a stop may be recorded' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER revocation_event_guard BEFORE UPDATE OR DELETE ON agent.revocation_event FOR EACH ROW EXECUTE FUNCTION agent.revocation_event_guard();
        ALTER TABLE agent.revocation_event ENABLE ROW LEVEL SECURITY;
        ALTER TABLE agent.revocation_event FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON agent.revocation_event USING ({TENANT}) WITH CHECK ({TENANT});
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE agent.revocation_event;
        DROP FUNCTION agent.revocation_event_guard();
    """)
