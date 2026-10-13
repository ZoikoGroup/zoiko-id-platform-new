"""SCIM 2.0 provisioning sources and resources.

Revision ID: provisioning_0001
Revises:
Create Date: 2026-10-13

ZID-AUTH-001 §11, §18 (AUTH-W6); ZID-OVERLAY-001 §10.
- platform.provisioning_source: one SCIM connector, bound to exactly one OAuth client (least
  privilege, revocable apart from interactive federation).
- platform.scim_resource: a provisioned User or Group as the source knows it. `externalId` is
  scoped to the source and never a Zoiko key; Users map to a principal. Only what SCIM must echo
  back is kept (userName, displayName, name, emails), classified CONFIDENTIAL.
- platform.scim_group_member: membership facts only - groups never create authority; removal is
  recorded (`removed_at`), nothing is deleted.
"""

from zid_platform.migrations import execute_script as _run

revision = "provisioning_0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = ("provisioning_source", "scim_resource", "scim_group_member")


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.provisioning_source (
            source_id      uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            client_id      text NOT NULL,
            display_name   text NOT NULL CHECK (char_length(display_name) <= 128),
            status         text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'SUSPENDED', 'RETIRED')),
            revision       integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
            created_at     timestamptz NOT NULL DEFAULT now(),
            updated_at     timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, client_id)
        );

        CREATE TABLE platform.scim_resource (
            resource_id    uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            source_id      uuid NOT NULL REFERENCES platform.provisioning_source (source_id),
            resource_type  text NOT NULL CHECK (resource_type IN ('User', 'Group')),
            external_id    text CHECK (external_id IS NULL OR char_length(external_id) <= 256),
            principal_id   uuid,
            user_name      text CHECK (user_name IS NULL OR char_length(user_name) <= 256),
            display_name   text CHECK (display_name IS NULL OR char_length(display_name) <= 256),
            active         boolean NOT NULL DEFAULT true,
            attributes     jsonb NOT NULL DEFAULT '{}',
            version        integer NOT NULL DEFAULT 1 CHECK (version >= 1),
            created_at     timestamptz NOT NULL,
            updated_at     timestamptz NOT NULL,
            deleted_at     timestamptz,
            CHECK (resource_type <> 'User' OR (principal_id IS NOT NULL AND user_name IS NOT NULL)),
            CHECK (resource_type <> 'Group' OR display_name IS NOT NULL)
        );
        CREATE UNIQUE INDEX scim_resource_external ON platform.scim_resource (source_id, resource_type, external_id)
            WHERE external_id IS NOT NULL AND deleted_at IS NULL;
        CREATE UNIQUE INDEX scim_resource_user_name ON platform.scim_resource (source_id, lower(user_name))
            WHERE resource_type = 'User' AND deleted_at IS NULL;

        CREATE TABLE platform.scim_group_member (
            group_id       uuid NOT NULL REFERENCES platform.scim_resource (resource_id),
            member_id      uuid NOT NULL REFERENCES platform.scim_resource (resource_id),
            tenant_id      uuid NOT NULL,
            added_at       timestamptz NOT NULL DEFAULT now(),
            removed_at     timestamptz,
            PRIMARY KEY (group_id, member_id)
        );
    """)
    for table in TENANT_TABLES:
        _run(f"""
            ALTER TABLE platform.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE platform.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON platform.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TABLE platform.scim_group_member;
        DROP TABLE platform.scim_resource;
        DROP TABLE platform.provisioning_source;
    """)
