"""A person's decision on an ownership candidate (ZID-GAP-001 v2.0 DTV-24; CR-103).

Revision ID: organization_0009
Revises: organization_0008
Create Date: 2026-11-16

ownership_confirmation: one decision for each candidate of one recorded graph version: CONFIRMED (a person confirms this person is a beneficial owner),
NOT_CONFIRMED, or ESCALATED, each with a reason and an evidence reference. The platform never makes this decision. The table repeats the rules: the reviewer is
never the person who recorded the graph (maker and checker), and a decision is never changed or deleted: a different view means a new graph version and a
new decision. Row-level security by company.
"""

from zid_platform.migrations import execute_script as _run

revision = "organization_0009"
down_revision = "organization_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE org.ownership_confirmation (
            confirmation_id  uuid PRIMARY KEY,
            tenant_id        uuid NOT NULL,
            graph_id         uuid NOT NULL REFERENCES org.ownership_graph (graph_id),
            entity_id        text NOT NULL CHECK (entity_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'),
            decision         text NOT NULL CHECK (decision IN ('CONFIRMED', 'NOT_CONFIRMED', 'ESCALATED')),
            reason           text NOT NULL CHECK (length(reason) BETWEEN 20 AND 2000),
            evidence_ref     text NOT NULL CHECK (length(evidence_ref) BETWEEN 1 AND 512),
            reviewer         uuid NOT NULL,
            created_at       timestamptz NOT NULL DEFAULT now(),
            UNIQUE (graph_id, entity_id)
        );

        CREATE FUNCTION org.ownership_confirmation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'a recorded ownership decision never changes and is never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.reviewer = (SELECT author FROM org.ownership_graph WHERE graph_id = NEW.graph_id) THEN
                RAISE EXCEPTION 'the person who recorded the graph does not decide its candidates' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER ownership_confirmation_guard BEFORE INSERT OR UPDATE OR DELETE ON org.ownership_confirmation
            FOR EACH ROW EXECUTE FUNCTION org.ownership_confirmation_guard();

        ALTER TABLE org.ownership_confirmation ENABLE ROW LEVEL SECURITY;
        ALTER TABLE org.ownership_confirmation FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON org.ownership_confirmation
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE org.ownership_confirmation;
        DROP FUNCTION org.ownership_confirmation_guard();
    """)
