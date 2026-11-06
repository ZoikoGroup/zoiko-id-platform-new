"""Organization hierarchy: a tree of units (group, subsidiary, division, department) and who belongs to each.

Revision ID: tenant_0016
Revises: tenant_0015
Create Date: 2026-11-05

Plan feature advanced.multi-org-hierarchy-group-governance. A company that is a group of organizations records the structure it governs: units under
units, up to six levels deep, and the people in each. It is a record of structure inside one tenant; it never reaches across tenants.
- platform.org_unit: one node. A parent is in the same tenant (the composite key enforces it); retired units stay for history.
- platform.org_unit_member: a person's place in a unit.
"""

from zid_platform.migrations import execute_script as _run

revision = "tenant_0016"
down_revision = "tenant_0015"
branch_labels = None
depends_on = None

TABLES = ("org_unit", "org_unit_member")


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.org_unit (
            unit_id        uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            parent_unit_id uuid,
            name           text NOT NULL CHECK (char_length(name) BETWEEN 2 AND 100),
            kind           text NOT NULL CHECK (kind IN ('GROUP', 'SUBSIDIARY', 'DIVISION', 'DEPARTMENT')),
            depth          integer NOT NULL CHECK (depth BETWEEN 0 AND 5),
            status         text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'RETIRED')),
            created_by     uuid NOT NULL,
            created_at     timestamptz NOT NULL DEFAULT now(),
            retired_at     timestamptz,
            UNIQUE (tenant_id, unit_id),
            FOREIGN KEY (tenant_id, parent_unit_id) REFERENCES platform.org_unit (tenant_id, unit_id),
            CHECK ((parent_unit_id IS NULL) = (depth = 0))
        );
        CREATE UNIQUE INDEX org_unit_name ON platform.org_unit (tenant_id, coalesce(parent_unit_id, '00000000-0000-0000-0000-000000000000'::uuid), lower(name))
            WHERE status = 'ACTIVE';
        CREATE TABLE platform.org_unit_member (
            unit_id      uuid NOT NULL,
            principal_id uuid NOT NULL,
            tenant_id    uuid NOT NULL,
            title        text NOT NULL DEFAULT '' CHECK (char_length(title) <= 80),
            added_by     uuid NOT NULL,
            added_at     timestamptz NOT NULL DEFAULT now(),
            removed_at   timestamptz,
            PRIMARY KEY (unit_id, principal_id),
            FOREIGN KEY (tenant_id, unit_id) REFERENCES platform.org_unit (tenant_id, unit_id)
        );
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
    _run("DROP TABLE platform.org_unit_member; DROP TABLE platform.org_unit;")
