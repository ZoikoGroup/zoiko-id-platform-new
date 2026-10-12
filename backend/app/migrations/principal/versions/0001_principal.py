"""Principals and identifiers (ZID-DATA-001 §3.1-3.2; ZID-DOM-001 §4; FR-IDN-001..007).

Revision ID: principal_0001
Revises:
Create Date: 2026-10-12

Guards in the database as well as the domain code:
- security_revision can never decrease (trigger; INV-11), principal_id/tenant/type never change;
- identifier uniqueness is scoped (tenant + namespace + issuer, per uniqueness_scope), so the same
  email in two tenants never collides (DM-AT-02);
- FORCE ROW LEVEL SECURITY on both tables.
Differences from ZID-DATA-001 (CR-007): identifier keeps the domain's uniqueness_scope/scope_key
and stores the namespace as written; sponsor_principal_id on principal for AI agents (§4.2).
"""

from zid_platform.migrations import execute_script as _run

revision = "principal_0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = ("principal", "principal_identifier")


def upgrade() -> None:
    _run("""
        CREATE TYPE core.principal_type AS ENUM ('HUMAN', 'ORGANIZATION', 'ORG_UNIT', 'DEVICE', 'APPLICATION',
            'SERVICE', 'WORKLOAD', 'AI_AGENT', 'EXTERNAL_PRINCIPAL');
        CREATE TYPE core.principal_status AS ENUM ('PENDING', 'ACTIVE', 'SUSPENDED', 'DISABLED', 'SUPERSEDED', 'ERASED');
        CREATE TYPE core.verification_state AS ENUM ('UNVERIFIED', 'VERIFIED', 'STALE', 'REVOKED');
        CREATE TYPE core.uniqueness_scope AS ENUM ('GLOBAL', 'TENANT', 'ISSUER', 'ORGANIZATION', 'NONE');

        CREATE TABLE core.principal (
            principal_id                uuid PRIMARY KEY,
            tenant_id                   uuid NOT NULL,
            principal_type              core.principal_type NOT NULL,
            status                      core.principal_status NOT NULL,
            home_cell                   text NOT NULL,
            display_label               text,
            sponsor_principal_id        uuid,
            security_revision           bigint NOT NULL DEFAULT 0 CHECK (security_revision >= 0),
            row_version                 bigint NOT NULL DEFAULT 0,
            created_at                  timestamptz NOT NULL DEFAULT now(),
            disabled_at                 timestamptz,
            superseded_by_principal_id  uuid,
            CHECK (principal_type <> 'AI_AGENT' OR sponsor_principal_id IS NOT NULL),
            CHECK (status <> 'SUPERSEDED' OR superseded_by_principal_id IS NOT NULL),
            UNIQUE (tenant_id, principal_id)
        );
        CREATE INDEX principal_tenant_type_status ON core.principal (tenant_id, principal_type, status);

        CREATE FUNCTION core.principal_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.security_revision < OLD.security_revision THEN
                RAISE EXCEPTION 'security_revision may not decrease' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.principal_id <> OLD.principal_id OR NEW.tenant_id <> OLD.tenant_id
               OR NEW.principal_type <> OLD.principal_type THEN
                RAISE EXCEPTION 'principal identity is immutable' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status = 'ERASED' THEN
                RAISE EXCEPTION 'erased principals are tombstones' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER principal_guard BEFORE UPDATE ON core.principal
            FOR EACH ROW EXECUTE FUNCTION core.principal_guard();

        CREATE TABLE core.principal_identifier (
            identifier_id           uuid PRIMARY KEY,
            tenant_id               uuid NOT NULL,
            principal_id            uuid NOT NULL,
            namespace               text NOT NULL,
            issuer                  text,
            uniqueness_scope        core.uniqueness_scope NOT NULL,
            scope_key               text NOT NULL DEFAULT '',
            normalized_value_hash   bytea NOT NULL,
            hash_algorithm          text NOT NULL DEFAULT 'sha256',
            encrypted_value_ref     uuid,
            verification_state      core.verification_state NOT NULL DEFAULT 'UNVERIFIED',
            valid_from              timestamptz NOT NULL DEFAULT now(),
            valid_to                timestamptz,
            recorded_at             timestamptz NOT NULL DEFAULT now(),
            superseded_at           timestamptz,
            FOREIGN KEY (tenant_id, principal_id) REFERENCES core.principal (tenant_id, principal_id)
        );
        CREATE UNIQUE INDEX identifier_unique_tenant ON core.principal_identifier
            (tenant_id, namespace, normalized_value_hash)
            WHERE superseded_at IS NULL AND uniqueness_scope = 'TENANT';
        CREATE UNIQUE INDEX identifier_unique_scoped ON core.principal_identifier
            (tenant_id, uniqueness_scope, scope_key, namespace, normalized_value_hash)
            WHERE superseded_at IS NULL AND uniqueness_scope IN ('ISSUER', 'ORGANIZATION');
        CREATE UNIQUE INDEX identifier_unique_global ON core.principal_identifier
            (namespace, normalized_value_hash)
            WHERE superseded_at IS NULL AND uniqueness_scope = 'GLOBAL';
        CREATE INDEX identifier_principal ON core.principal_identifier (principal_id, verification_state);
    """)
    for table in TENANT_TABLES:
        _run(f"""
            ALTER TABLE core.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE core.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON core.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TABLE core.principal_identifier;
        DROP TRIGGER principal_guard ON core.principal;
        DROP FUNCTION core.principal_guard();
        DROP TABLE core.principal;
        DROP TYPE core.uniqueness_scope;
        DROP TYPE core.verification_state;
        DROP TYPE core.principal_status;
        DROP TYPE core.principal_type;
    """)
