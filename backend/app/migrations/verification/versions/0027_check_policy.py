"""A company's WF-05 verification policy per market and purpose (ZID-DTV-WF-05 A11; CR-118).

Revision ID: verification_0027
Revises: verification_0026
Create Date: 2026-11-18

check_policy: DRAFT, SUBMITTED, then APPROVED or REJECTED; an approval supersedes the earlier approved policy of the same market and purpose. The
approver is never the author; a decided policy never changes except to be superseded; rows are never deleted; at most one APPROVED policy per market
and purpose. Tenant data under row-level security.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0027"
down_revision = "verification_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.check_policy (
            policy_id        uuid PRIMARY KEY,
            tenant_id        uuid NOT NULL,
            market           text NOT NULL CHECK (market ~ '^[A-Z]{2}$'),
            purpose          text NOT NULL CHECK (purpose ~ '^[A-Z][A-Z0-9_]{1,63}$'),
            required_methods text[] NOT NULL,
            document_types   jsonb NOT NULL,
            status           text NOT NULL CHECK (status IN ('DRAFT', 'SUBMITTED', 'APPROVED', 'REJECTED', 'SUPERSEDED')),
            author           text NOT NULL CHECK (length(btrim(author)) BETWEEN 1 AND 128),
            created_at       timestamptz NOT NULL,
            approver         text,
            decided_at       timestamptz,
            decision_reason  text,
            CHECK (approver IS NULL OR approver <> author),
            CHECK (status NOT IN ('APPROVED', 'REJECTED', 'SUPERSEDED') OR (approver IS NOT NULL AND decided_at IS NOT NULL))
        );
        CREATE UNIQUE INDEX check_policy_one_approved ON verify.check_policy (tenant_id, market, purpose) WHERE status = 'APPROVED';

        CREATE FUNCTION verify.check_policy_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'policies are kept' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.policy_id, NEW.tenant_id, NEW.market, NEW.purpose, NEW.required_methods, NEW.document_types, NEW.author, NEW.created_at)
               IS DISTINCT FROM
               (OLD.policy_id, OLD.tenant_id, OLD.market, OLD.purpose, OLD.required_methods, OLD.document_types, OLD.author, OLD.created_at) THEN
                RAISE EXCEPTION 'a policy is not edited; write a new one' USING ERRCODE = 'check_violation';
            END IF;
            IF NOT ((OLD.status = 'DRAFT' AND NEW.status = 'SUBMITTED') OR (OLD.status = 'SUBMITTED' AND NEW.status IN ('APPROVED', 'REJECTED'))
                    OR (OLD.status = 'APPROVED' AND NEW.status = 'SUPERSEDED')) THEN
                RAISE EXCEPTION 'a policy cannot move from % to %', OLD.status, NEW.status USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER check_policy_guard BEFORE UPDATE OR DELETE ON verify.check_policy
            FOR EACH ROW EXECUTE FUNCTION verify.check_policy_guard();

        ALTER TABLE verify.check_policy ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.check_policy FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.check_policy
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.check_policy;
        DROP FUNCTION verify.check_policy_guard();
    """)
