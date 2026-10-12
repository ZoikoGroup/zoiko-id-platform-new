"""Policy definitions and immutable releases (ZID-DATA-001 §7.3; ZID-AUTHZ-001 §18; DEC-C3).

Revision ID: policy_authoring_0001
Revises:
Create Date: 2026-10-12

Release states follow ZID-AUTHZ-001 §18 (wider than ZID-DATA-001: VALIDATING, REVIEW, SCHEDULED,
REVOKED; CR-007). Database guards: one ACTIVE release per tenant; once a release leaves DRAFT its
source, schema version and test cases never change (the content hash is what evidence cites).
"""

from zid_platform.migrations import execute_script as _run

revision = "policy_authoring_0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = ("policy_definition", "policy_release")


def upgrade() -> None:
    _run("""
        CREATE TYPE authz.policy_release_status AS ENUM ('DRAFT', 'VALIDATING', 'REVIEW', 'SCHEDULED', 'ACTIVE',
            'RETIRED', 'REVOKED');

        CREATE TABLE authz.policy_definition (
            policy_id       uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            policy_name     text NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, policy_name)
        );

        CREATE TABLE authz.policy_release (
            release_id          uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            policy_id           uuid NOT NULL REFERENCES authz.policy_definition (policy_id),
            version             integer NOT NULL CHECK (version >= 1),
            source_language     text NOT NULL DEFAULT 'cedar',
            source_text         text NOT NULL,
            source_hash         text NOT NULL,
            schema_version      text NOT NULL,
            cases               jsonb NOT NULL DEFAULT '[]',
            status              authz.policy_release_status NOT NULL,
            author_principal_id uuid NOT NULL,
            reviewers           uuid[] NOT NULL DEFAULT '{}',
            released_at         timestamptz,
            UNIQUE (policy_id, version)
        );
        CREATE UNIQUE INDEX one_active_release_per_tenant ON authz.policy_release (tenant_id) WHERE status = 'ACTIVE';

        CREATE FUNCTION authz.policy_release_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status <> 'DRAFT' AND (NEW.source_text, NEW.source_hash, NEW.schema_version, NEW.cases)
                IS DISTINCT FROM (OLD.source_text, OLD.source_hash, OLD.schema_version, OLD.cases) THEN
                RAISE EXCEPTION 'published policy source is immutable; create a new release'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status IN ('RETIRED', 'REVOKED') AND NEW.status <> OLD.status THEN
                RAISE EXCEPTION 'retired/revoked releases are final' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER policy_release_guard BEFORE UPDATE ON authz.policy_release
            FOR EACH ROW EXECUTE FUNCTION authz.policy_release_guard();
    """)
    for table in TENANT_TABLES:
        _run(f"""
            ALTER TABLE authz.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE authz.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON authz.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TRIGGER policy_release_guard ON authz.policy_release;
        DROP FUNCTION authz.policy_release_guard();
        DROP TABLE authz.policy_release;
        DROP TABLE authz.policy_definition;
        DROP TYPE authz.policy_release_status;
    """)
