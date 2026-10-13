"""RC-4 sovereign / lockbox recovery policy.

Revision ID: authentication_0004
Revises: authentication_0003
Create Date: 2026-10-13

ZID-AUTH-001 §14 (RC-4). A principal may be placed under a lockbox policy: recovery then needs
IAL-Z3 re-proofing, approval by at least k (>= 2) of its *designated* approvers - not any
administrator - and a cooling-off of at least 7 days. Setting, changing or lifting the policy is
itself an approved R3 change, and the principal can never approve its own recovery.
An RC-4 case may live up to 35 days (other classes 7): the cooling-off alone is at least 7.
"""

from zid_platform.migrations import execute_script as _run

revision = "authentication_0004"
down_revision = "authentication_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE authn.recovery_policy (
            tenant_id          uuid NOT NULL,
            principal_id       uuid PRIMARY KEY,
            approvers          uuid[] NOT NULL CHECK (cardinality(approvers) BETWEEN 2 AND 5),
            k                  smallint NOT NULL CHECK (k >= 2),
            cooling_off_hours  integer NOT NULL CHECK (cooling_off_hours BETWEEN 168 AND 720),
            revision           integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
            updated_by         uuid NOT NULL,
            updated_at         timestamptz NOT NULL,
            CHECK (k <= cardinality(approvers)),
            CHECK (NOT (principal_id = ANY (approvers)))
        );
        ALTER TABLE authn.recovery_policy ENABLE ROW LEVEL SECURITY;
        ALTER TABLE authn.recovery_policy FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON authn.recovery_policy
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

        DO $$
        DECLARE c text;
        BEGIN
            SELECT conname INTO STRICT c FROM pg_constraint
             WHERE conrelid = 'authn.recovery_case'::regclass AND contype = 'c'
               AND pg_get_constraintdef(oid) LIKE '%7 days%';
            EXECUTE format('ALTER TABLE authn.recovery_case DROP CONSTRAINT %I', c);
        END $$;
        ALTER TABLE authn.recovery_case ADD CONSTRAINT recovery_case_ttl
            CHECK (expires_at <= created_at + CASE WHEN recovery_class = 'RC-4'
                   THEN interval '35 days' ELSE interval '7 days' END);
    """)


def downgrade() -> None:
    _run("""
        ALTER TABLE authn.recovery_case DROP CONSTRAINT recovery_case_ttl;
        ALTER TABLE authn.recovery_case ADD CONSTRAINT recovery_case_ttl
            CHECK (expires_at <= created_at + interval '7 days');
        DROP TABLE authn.recovery_policy;
    """)
