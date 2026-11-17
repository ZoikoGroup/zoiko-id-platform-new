"""An applicant's appeal against a company-verification decision (ZID-DTV-WF-02 K06, K10; CR-109).

Revision ID: organization_0013
Revises: organization_0012
Create Date: 2026-11-17

kyb_appeal: one appeal against one assessment version. It opens as OPEN and is decided once as UPHELD or ALLOWED, or withdrawn, with the decider and the reasons; after that it never changes
and nothing is deleted. At most one appeal is open for a decision (a unique index), and the decider is never the person who opened it (the table repeats the rule; who made the decision and who the
applicant is are checked by the service, which can see them). Row-level security by company.
"""

from zid_platform.migrations import execute_script as _run

revision = "organization_0013"
down_revision = "organization_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE org.kyb_appeal (
            appeal_id        uuid PRIMARY KEY,
            tenant_id        uuid NOT NULL,
            decision_version integer NOT NULL CHECK (decision_version >= 1),
            opened_by        uuid NOT NULL,
            reason           text NOT NULL CHECK (length(reason) BETWEEN 20 AND 2000),
            state            text NOT NULL DEFAULT 'OPEN' CHECK (state IN ('OPEN', 'UPHELD', 'ALLOWED', 'WITHDRAWN')),
            opened_at        timestamptz NOT NULL,
            decided_by       uuid,
            decided_at       timestamptz,
            decision_reason  text CHECK (decision_reason IS NULL OR length(decision_reason) BETWEEN 20 AND 2000),
            CHECK ((state = 'OPEN') = (decided_at IS NULL)),
            CHECK (state IN ('OPEN', 'WITHDRAWN') OR (decided_by IS NOT NULL AND decision_reason IS NOT NULL)),
            CHECK (decided_by IS NULL OR decided_by <> opened_by)
        );
        CREATE UNIQUE INDEX kyb_appeal_one_open ON org.kyb_appeal (tenant_id, decision_version) WHERE state = 'OPEN';

        CREATE FUNCTION org.kyb_appeal_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'an appeal is never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.state <> 'OPEN' THEN
                RAISE EXCEPTION 'a decided appeal never changes' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.appeal_id <> OLD.appeal_id OR NEW.tenant_id <> OLD.tenant_id OR NEW.decision_version <> OLD.decision_version
               OR NEW.opened_by <> OLD.opened_by OR NEW.reason <> OLD.reason OR NEW.opened_at <> OLD.opened_at THEN
                RAISE EXCEPTION 'an appeal changes only by being decided' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER kyb_appeal_guard BEFORE UPDATE OR DELETE ON org.kyb_appeal
            FOR EACH ROW EXECUTE FUNCTION org.kyb_appeal_guard();

        ALTER TABLE org.kyb_appeal ENABLE ROW LEVEL SECURITY;
        ALTER TABLE org.kyb_appeal FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON org.kyb_appeal
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE org.kyb_appeal;
        DROP FUNCTION org.kyb_appeal_guard();
    """)
