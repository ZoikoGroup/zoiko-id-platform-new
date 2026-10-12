"""Organizations, authority roots, source events (ZID-DATA-001 §4.1, §4.3; ZID-ROOT-001 §13, §17-18).

Revision ID: organization_0001
Revises:
Create Date: 2026-10-12

authority_root_assertion follows ZID-ROOT-001 §13, which is wider than ZID-DATA-001 §4.3 (CR-007):
facts (root_capability_class), VLEI_OOR/VLEI_ECR/POWER_OF_ATTORNEY types, PENDING/CONFLICTED/
SUPERSEDED states, jurisdiction, source revision/effective/observed times, and the authority scope
(actions, value ceiling) that SCOPE facts need. org.source_event makes source replays idempotent
(ROOT-24). authority_root_revision on the organization only increases (trigger).
"""

from zid_platform.migrations import execute_script as _run

revision = "organization_0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = ("organization", "authority_root_assertion", "source_event")


def upgrade() -> None:
    _run("""
        CREATE TYPE org.verification_status AS ENUM ('UNVERIFIED', 'PENDING', 'VERIFIED', 'STALE', 'REVOKED');
        CREATE TYPE org.root_fact AS ENUM ('ENTITY_EXISTENCE', 'OFFICIAL_ROLE', 'SIGNING_POWER', 'CONTROL',
            'DELEGATION_BASIS');
        CREATE TYPE org.root_type AS ENUM ('REGISTRY', 'VLEI_OOR', 'VLEI_ECR', 'BOARD_RESOLUTION', 'SIGNING_MATRIX',
            'POWER_OF_ATTORNEY', 'INSTITUTIONAL_KYB', 'OTHER');
        CREATE TYPE org.root_class AS ENUM ('AR0', 'AR1', 'AR2', 'AR3');
        CREATE TYPE org.root_status AS ENUM ('PENDING', 'ACTIVE', 'STALE', 'CONFLICTED', 'SUSPENDED', 'REVOKED',
            'EXPIRED', 'SUPERSEDED');

        CREATE TABLE org.organization (
            organization_id         uuid PRIMARY KEY,
            tenant_id               uuid NOT NULL,
            legal_name              text NOT NULL,
            jurisdiction            text,
            registration_namespace  text,
            registration_id_hash    bytea,
            verification_status     org.verification_status NOT NULL DEFAULT 'UNVERIFIED',
            can_originate_authority boolean NOT NULL DEFAULT true,
            authority_root_revision bigint NOT NULL DEFAULT 0 CHECK (authority_root_revision >= 0),
            valid_from              timestamptz NOT NULL,
            valid_to                timestamptz,
            UNIQUE (tenant_id, organization_id),
            UNIQUE (tenant_id, jurisdiction, registration_namespace, registration_id_hash)
        );

        CREATE FUNCTION org.organization_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.authority_root_revision < OLD.authority_root_revision THEN
                RAISE EXCEPTION 'authority_root_revision may not decrease' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER organization_guard BEFORE UPDATE ON org.organization
            FOR EACH ROW EXECUTE FUNCTION org.organization_guard();

        CREATE TABLE org.authority_root_assertion (
            root_assertion_id           uuid PRIMARY KEY,
            tenant_id                   uuid NOT NULL,
            organization_principal_id   uuid NOT NULL,
            holder_principal_id         uuid NOT NULL,
            root_type                   org.root_type NOT NULL,
            facts                       org.root_fact[] NOT NULL CHECK (cardinality(facts) > 0),
            assurance_class             org.root_class NOT NULL,
            status                      org.root_status NOT NULL,
            jurisdiction                text NOT NULL,
            scope_actions               text[],
            value_limit                 numeric(38,9) CHECK (value_limit >= 0),
            currency                    char(3),
            source_uri_or_ref           text,
            source_revision             text NOT NULL DEFAULT '',
            source_effective_at         timestamptz,
            observed_at                 timestamptz NOT NULL DEFAULT now(),
            evidence_object_id          uuid,
            fresh_until                 timestamptz NOT NULL,
            valid_from                  timestamptz NOT NULL,
            valid_to                    timestamptz,
            recorded_at                 timestamptz NOT NULL DEFAULT now(),
            row_version                 bigint NOT NULL DEFAULT 0,
            CHECK (value_limit IS NULL OR currency IS NOT NULL),
            FOREIGN KEY (tenant_id, organization_principal_id) REFERENCES org.organization (tenant_id, organization_id)
        );
        CREATE INDEX root_org_holder ON org.authority_root_assertion (tenant_id, organization_principal_id, holder_principal_id);
        CREATE INDEX root_refresh ON org.authority_root_assertion (status, fresh_until);

        CREATE TABLE org.source_event (
            tenant_id           uuid NOT NULL,
            source_event_id     text NOT NULL,
            organization_id     uuid NOT NULL,
            kind                text NOT NULL,
            processed_at        timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, source_event_id)
        );
    """)
    for table in TENANT_TABLES:
        _run(f"""
            ALTER TABLE org.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE org.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON org.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TABLE org.source_event;
        DROP TABLE org.authority_root_assertion;
        DROP TRIGGER organization_guard ON org.organization;
        DROP FUNCTION org.organization_guard();
        DROP TABLE org.organization;
        DROP TYPE org.root_status;
        DROP TYPE org.root_class;
        DROP TYPE org.root_type;
        DROP TYPE org.root_fact;
        DROP TYPE org.verification_status;
    """)
