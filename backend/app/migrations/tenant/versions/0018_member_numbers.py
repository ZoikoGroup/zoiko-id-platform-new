"""A short, permanent member number for every person in a company (CR-061).

Revision ID: tenant_0018
Revises: tenant_0017
Create Date: 2026-11-06

People were known only by a UUID, which is unreadable in an approval, a team list or an audit trail. This gives each person a number inside their
company (M-0001, M-0002, ...) that is assigned once, never changes and is never reused, so "M-0003 approved" stays true forever.

- platform.tenant_member_no: (tenant, person) -> number. Row-level security like every tenant table.
- A trigger on platform.tenant_role_assignment numbers a person the first time they get any role, whichever code path created it (invitation,
  owner bootstrap, owner change, provisioning). A per-company advisory lock makes the next number safe under concurrency.
- The existing people are numbered in the order they first appeared in their company.
"""

from zid_platform.migrations import execute_script as _run

revision = "tenant_0018"
down_revision = "tenant_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.tenant_member_no (
            tenant_id    uuid        NOT NULL REFERENCES platform.tenant (tenant_id),
            principal_id uuid        NOT NULL,
            member_no    integer     NOT NULL CHECK (member_no >= 1),
            assigned_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (tenant_id, principal_id),
            UNIQUE (tenant_id, member_no)
        );

        CREATE FUNCTION platform.member_no_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'a member number is permanent: it is never changed, reused or removed';
        END $$;
        CREATE TRIGGER member_no_guard BEFORE UPDATE OR DELETE ON platform.tenant_member_no
            FOR EACH ROW EXECUTE FUNCTION platform.member_no_guard();

        -- the people who already exist, numbered in the order they first appeared. Row-level security is forced on the assignment table, which
        -- binds even its owner, so the force is lifted for this one read and restored.
        DO $$
        DECLARE forced boolean;
        BEGIN
            SELECT relforcerowsecurity INTO forced FROM pg_class WHERE oid = 'platform.tenant_role_assignment'::regclass;
            IF forced THEN ALTER TABLE platform.tenant_role_assignment NO FORCE ROW LEVEL SECURITY; END IF;
            INSERT INTO platform.tenant_member_no (tenant_id, principal_id, member_no, assigned_at)
                SELECT tenant_id, principal_id,
                       row_number() OVER (PARTITION BY tenant_id ORDER BY first_at, principal_id),
                       first_at
                  FROM (SELECT tenant_id, principal_id, min(created_at) AS first_at
                          FROM platform.tenant_role_assignment GROUP BY tenant_id, principal_id) s;
            IF forced THEN ALTER TABLE platform.tenant_role_assignment FORCE ROW LEVEL SECURITY; END IF;
        END $$;

        ALTER TABLE platform.tenant_member_no ENABLE ROW LEVEL SECURITY;
        ALTER TABLE platform.tenant_member_no FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON platform.tenant_member_no
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

        CREATE FUNCTION platform.assign_member_no() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM pg_advisory_xact_lock(hashtext('member_no:' || NEW.tenant_id::text));
            INSERT INTO platform.tenant_member_no (tenant_id, principal_id, member_no)
                SELECT NEW.tenant_id, NEW.principal_id, coalesce(max(member_no), 0) + 1
                  FROM platform.tenant_member_no WHERE tenant_id = NEW.tenant_id
                ON CONFLICT (tenant_id, principal_id) DO NOTHING;
            RETURN NEW;
        END $$;
        CREATE TRIGGER assign_member_no AFTER INSERT ON platform.tenant_role_assignment
            FOR EACH ROW EXECUTE FUNCTION platform.assign_member_no();
    """)


def downgrade() -> None:
    _run("""
        DROP TRIGGER assign_member_no ON platform.tenant_role_assignment;
        DROP FUNCTION platform.assign_member_no();
        DROP TRIGGER member_no_guard ON platform.tenant_member_no;
        DROP FUNCTION platform.member_no_guard();
        DROP TABLE platform.tenant_member_no;
    """)
