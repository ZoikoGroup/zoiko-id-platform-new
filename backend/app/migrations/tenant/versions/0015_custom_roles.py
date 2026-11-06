"""Custom organization roles: a company's own named roles, each a narrowing of a standard role.

Revision ID: tenant_0015
Revises: tenant_0014
Create Date: 2026-11-05

Plan feature advanced.custom-organization-roles-permission-models. A custom role names a subset of what one standard role (OPERATOR or VIEWER)
may already do. It can only narrow: a person who holds one still needs the base role, and the role matrix decides first. A custom role is
never a way to hold more than the base role, and never reaches OWNER, ADMIN or APPROVER.
- platform.custom_role: the definition (name, base role, the matrix actions it keeps).
- platform.custom_role_assignment: who holds it. Revoked rows stay as history.
"""

from zid_platform.migrations import execute_script as _run

revision = "tenant_0015"
down_revision = "tenant_0014"
branch_labels = None
depends_on = None

TABLES = ("custom_role", "custom_role_assignment")


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.custom_role (
            role_id     uuid PRIMARY KEY,
            tenant_id   uuid NOT NULL,
            name        text NOT NULL CHECK (char_length(name) BETWEEN 2 AND 64),
            description text NOT NULL DEFAULT '' CHECK (char_length(description) <= 300),
            base_role   text NOT NULL CHECK (base_role IN ('OPERATOR', 'VIEWER')),
            actions     text[] NOT NULL CHECK (cardinality(actions) BETWEEN 1 AND 200),
            status      text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'RETIRED')),
            created_by  uuid NOT NULL,
            created_at  timestamptz NOT NULL DEFAULT now(),
            retired_at  timestamptz
        );
        CREATE UNIQUE INDEX custom_role_name ON platform.custom_role (tenant_id, lower(name)) WHERE status = 'ACTIVE';
        CREATE TABLE platform.custom_role_assignment (
            role_id      uuid NOT NULL REFERENCES platform.custom_role (role_id),
            principal_id uuid NOT NULL,
            tenant_id    uuid NOT NULL,
            assigned_by  uuid NOT NULL,
            assigned_at  timestamptz NOT NULL DEFAULT now(),
            revoked_at   timestamptz,
            PRIMARY KEY (role_id, principal_id)
        );
        CREATE INDEX custom_role_assignment_principal ON platform.custom_role_assignment (principal_id) WHERE revoked_at IS NULL;
    """)
    for t in TABLES:
        _run(f"""
            ALTER TABLE platform.{t} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE platform.{t} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON platform.{t}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("DROP TABLE platform.custom_role_assignment; DROP TABLE platform.custom_role;")
