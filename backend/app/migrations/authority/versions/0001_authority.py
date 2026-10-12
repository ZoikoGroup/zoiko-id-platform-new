"""Authority grants and normalized scopes (ZID-DATA-001 §7.1-7.2; ZID-MANDATE-001 §3-6; DEC-C1, DEC-C2).

Revision ID: authority_0001
Revises:
Create Date: 2026-10-12

authz.authority_grant follows ZID-MANDATE-001 §3, wider than ZID-DATA-001 §7.1 (CR-007): grant_type,
continuity_mode (CR-001), PENDING_APPROVAL/REVALIDATION_PENDING/SUPERSEDED states (CR-002), purpose,
tool and jurisdiction sets, value/count/data limits, minimum assurance, issued_at, evidence_ref.
NULL array = unrestricted dimension (explicit ANY); empty array = nothing.

Database guards: limits and depth non-negative; scope columns are immutable once written (material
mutation creates a superseding grant, MANDATE §6); terminal states never return to ACTIVE.
"""

from zid_platform.migrations import execute_script as _run

revision = "authority_0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = ("authority_grant", "resource_scope", "data_scope")


def upgrade() -> None:
    _run("""
        CREATE TYPE authz.grant_type AS ENUM ('PERSONAL', 'ROLE_ISSUED', 'ORGANIZATION_DIRECT', 'SERVICE_DELEGATION',
            'AGENT_DELEGATION', 'EXTERNAL_DELEGATION');
        CREATE TYPE authz.continuity_mode AS ENUM ('PERSON_BOUND', 'ROLE_CONTINUOUS', 'ORGANIZATION_CONTINUOUS',
            'CONTRACT_BOUND');
        CREATE TYPE authz.grant_status AS ENUM ('DRAFT', 'PENDING_APPROVAL', 'ACTIVE', 'REVALIDATION_PENDING',
            'SUSPENDED', 'REVOKED', 'EXPIRED', 'SUPERSEDED');

        CREATE TABLE authz.resource_scope (
            scope_id                uuid PRIMARY KEY,
            tenant_id               uuid NOT NULL,
            patterns                text[],
            canonical_expression    text NOT NULL,
            expression_hash         bytea NOT NULL,
            version                 integer NOT NULL DEFAULT 1,
            status                  text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'RETIRED')),
            UNIQUE (tenant_id, expression_hash, version)
        );
        CREATE TABLE authz.data_scope (
            scope_id                uuid PRIMARY KEY,
            tenant_id               uuid NOT NULL,
            categories              text[],
            canonical_expression    text NOT NULL,
            expression_hash         bytea NOT NULL,
            version                 integer NOT NULL DEFAULT 1,
            status                  text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'RETIRED')),
            UNIQUE (tenant_id, expression_hash, version)
        );

        CREATE TABLE authz.authority_grant (
            authority_grant_id          uuid PRIMARY KEY,
            tenant_id                   uuid NOT NULL,
            grant_type                  authz.grant_type NOT NULL,
            grantor_principal_id        uuid NOT NULL,
            grantee_principal_id        uuid NOT NULL,
            represented_principal_id    uuid NOT NULL,
            authority_root_assertion_id uuid,
            parent_grant_id             uuid REFERENCES authz.authority_grant (authority_grant_id),
            actions                     text[],
            resource_scope_id           uuid NOT NULL REFERENCES authz.resource_scope (scope_id),
            purposes                    text[],
            data_scope_id               uuid NOT NULL REFERENCES authz.data_scope (scope_id),
            jurisdictions               text[],
            tools                       text[],
            value_limit                 numeric(38,9) CHECK (value_limit >= 0),
            currency                    char(3),
            count_limit                 bigint CHECK (count_limit >= 0),
            data_limit_bytes            bigint CHECK (data_limit_bytes >= 0),
            min_assurance               smallint NOT NULL DEFAULT 0 CHECK (min_assurance BETWEEN 0 AND 3),
            delegation_allowed          boolean NOT NULL,
            max_delegation_depth        smallint NOT NULL CHECK (max_delegation_depth >= 0),
            continuity_mode             authz.continuity_mode NOT NULL,
            status                      authz.grant_status NOT NULL,
            issued_at                   timestamptz NOT NULL,
            valid_from                  timestamptz,
            valid_to                    timestamptz,
            source_policy_release_id    uuid,
            evidence_ref                uuid,
            revision                    bigint NOT NULL DEFAULT 0,
            row_version                 bigint NOT NULL DEFAULT 0,
            CHECK (value_limit IS NULL OR currency IS NOT NULL),
            CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from),
            CHECK (parent_grant_id IS NOT NULL OR authority_root_assertion_id IS NOT NULL),
            CHECK (parent_grant_id IS NULL OR parent_grant_id <> authority_grant_id)
        );
        CREATE INDEX grant_grantee ON authz.authority_grant (tenant_id, grantee_principal_id, status, valid_to);
        CREATE INDEX grant_represented ON authz.authority_grant (tenant_id, represented_principal_id, status);
        CREATE INDEX grant_parent ON authz.authority_grant (parent_grant_id);
        CREATE INDEX grant_root ON authz.authority_grant (authority_root_assertion_id, status);

        CREATE FUNCTION authz.authority_grant_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent_depth smallint;
        BEGIN
            IF TG_OP = 'INSERT' AND NEW.parent_grant_id IS NOT NULL THEN
                SELECT max_delegation_depth INTO parent_depth FROM authz.authority_grant
                    WHERE authority_grant_id = NEW.parent_grant_id;
                IF parent_depth IS NULL OR NEW.max_delegation_depth >= parent_depth THEN
                    RAISE EXCEPTION 'delegation depth must strictly decrease' USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            IF TG_OP = 'UPDATE' THEN
                IF OLD.status IN ('REVOKED', 'EXPIRED', 'SUPERSEDED') AND NEW.status <> OLD.status THEN
                    RAISE EXCEPTION 'terminal grant states are final' USING ERRCODE = 'check_violation';
                END IF;
                IF (NEW.actions, NEW.resource_scope_id, NEW.purposes, NEW.data_scope_id, NEW.jurisdictions,
                    NEW.tools, NEW.value_limit, NEW.currency, NEW.count_limit, NEW.data_limit_bytes,
                    NEW.min_assurance, NEW.delegation_allowed, NEW.max_delegation_depth, NEW.continuity_mode,
                    NEW.grantor_principal_id, NEW.grantee_principal_id, NEW.represented_principal_id,
                    NEW.parent_grant_id, NEW.authority_root_assertion_id, NEW.valid_from, NEW.valid_to)
                   IS DISTINCT FROM
                   (OLD.actions, OLD.resource_scope_id, OLD.purposes, OLD.data_scope_id, OLD.jurisdictions,
                    OLD.tools, OLD.value_limit, OLD.currency, OLD.count_limit, OLD.data_limit_bytes,
                    OLD.min_assurance, OLD.delegation_allowed, OLD.max_delegation_depth, OLD.continuity_mode,
                    OLD.grantor_principal_id, OLD.grantee_principal_id, OLD.represented_principal_id,
                    OLD.parent_grant_id, OLD.authority_root_assertion_id, OLD.valid_from, OLD.valid_to) THEN
                    RAISE EXCEPTION 'grant scope is immutable; create a superseding grant'
                        USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER authority_grant_guard BEFORE INSERT OR UPDATE ON authz.authority_grant
            FOR EACH ROW EXECUTE FUNCTION authz.authority_grant_guard();
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
        DROP TABLE authz.authority_grant;
        DROP FUNCTION authz.authority_grant_guard();
        DROP TABLE authz.data_scope;
        DROP TABLE authz.resource_scope;
        DROP TYPE authz.grant_status;
        DROP TYPE authz.continuity_mode;
        DROP TYPE authz.grant_type;
    """)
