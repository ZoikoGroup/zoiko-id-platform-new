"""A company's verification grade, as it was assessed (ZID-DTV-WF-02 section 4; CR-109).

Revision ID: organization_0010
Revises: organization_0009
Create Date: 2026-11-17

kyb_assessment: one row per assessment of a company, newest version last. It holds the grade, the claims proven and the reasons it is not higher, and the plain facts the grade was worked
out from (ids and codes only), so the grade can be worked out AGAIN at the moment of an action: a mandate that has ended or a register check that has gone stale lowers what is allowed
without anyone editing a row. A row is never changed or deleted: a different view is a new version. Row-level security by company.
"""

from zid_platform.migrations import execute_script as _run

revision = "organization_0010"
down_revision = "organization_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE org.kyb_assessment (
            assessment_id  uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            version        integer NOT NULL CHECK (version >= 1),
            grade          text NOT NULL CHECK (grade IN ('K0', 'K1', 'K2', 'K3', 'K4')),
            claims         jsonb NOT NULL DEFAULT '[]'::jsonb,
            reasons        jsonb NOT NULL DEFAULT '[]'::jsonb,
            facts          jsonb NOT NULL,
            policy_hash    text,
            trigger        text NOT NULL CHECK (length(trigger) BETWEEN 1 AND 80),
            actor          uuid,
            recheck_due    timestamptz,
            assessed_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, version)
        );

        CREATE FUNCTION org.kyb_assessment_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'a recorded company assessment never changes and is never deleted' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER kyb_assessment_guard BEFORE UPDATE OR DELETE ON org.kyb_assessment
            FOR EACH ROW EXECUTE FUNCTION org.kyb_assessment_guard();

        ALTER TABLE org.kyb_assessment ENABLE ROW LEVEL SECURITY;
        ALTER TABLE org.kyb_assessment FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON org.kyb_assessment
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE org.kyb_assessment;
        DROP FUNCTION org.kyb_assessment_guard();
    """)
