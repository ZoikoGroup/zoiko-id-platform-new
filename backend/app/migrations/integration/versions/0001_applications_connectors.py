"""Application registry and enterprise connector framework (ZID-OVERLAY-001 §14-16, §24; G-W2;
CR-015).

Revision ID: integration_0001
Revises:
Create Date: 2026-10-22

- integration_application: the §14 registry (exact endpoints, resource ids, credential profile, PEP
  profile, risk/evidence floors, owner, status, monotonic configuration revision).
- integration_connector: the §15 connector contract over the module that does the work (federation
  connection or SCIM source via `source_ref`): capabilities, source-of-truth scope, auth profile,
  health, freshness, consecutive failures, cursor, owner, disable behaviour, quarantine reason.
- connector_credential: write-only credential versions (§16): the value is envelope-encrypted under
  the cell's DATA_KEK and never returned; only a fingerprint commitment is ever shown. One ACTIVE
  version at a time; a rotated-out version stays RETIRING until its bounded overlap ends.
All tenant-scoped under RLS.
"""

from zid_platform.migrations import execute_script as _run

revision = "integration_0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES = ("integration_application", "integration_connector", "connector_credential")


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.integration_application (
            application_id          uuid PRIMARY KEY,
            tenant_id               uuid NOT NULL,
            name                    text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
            environment             text NOT NULL CHECK (environment IN ('DEV', 'TEST', 'STAGE', 'PROD')),
            protocols               text[] NOT NULL CHECK (cardinality(protocols) > 0),
            redirect_uris           text[] NOT NULL DEFAULT '{}',
            resource_ids            text[] NOT NULL DEFAULT '{}',
            credential_profile      text NOT NULL CHECK (credential_profile IN ('private_key_jwt', 'mtls',
                                    'workload_identity', 'secret_exception')),
            credential_justification text,
            pep_obligations         text[] NOT NULL DEFAULT '{}',
            pep_context_fields      text[] NOT NULL DEFAULT '{}',
            risk_floor              smallint NOT NULL DEFAULT 1 CHECK (risk_floor BETWEEN 0 AND 3),
            evidence_floor          smallint NOT NULL DEFAULT 1 CHECK (evidence_floor BETWEEN 0 AND 3),
            owner_principal_id      uuid NOT NULL,
            status                  text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'TESTING',
                                    'ACTIVE', 'SUSPENDED', 'RETIRED')),
            configuration_revision  bigint NOT NULL DEFAULT 1,
            created_at              timestamptz NOT NULL DEFAULT now(),
            updated_at              timestamptz NOT NULL DEFAULT now(),
            CHECK (credential_profile <> 'secret_exception' OR length(credential_justification) >= 20),
            CHECK (evidence_floor >= risk_floor)
        );

        CREATE TABLE platform.integration_connector (
            connector_id            uuid PRIMARY KEY,
            tenant_id               uuid NOT NULL,
            connector_type          text NOT NULL CHECK (connector_type IN ('OIDC_IDP', 'SAML_IDP', 'SCIM',
                                    'HRIS', 'SIGNALS', 'WEBHOOK', 'DIRECTORY')),
            display_name            text NOT NULL,
            source_ref              text,
            capabilities            text[] NOT NULL CHECK (cardinality(capabilities) > 0),
            source_of_truth_scope   text[] NOT NULL DEFAULT '{}',
            auth_profile            text NOT NULL CHECK (auth_profile IN ('OAUTH_CLIENT', 'MTLS',
                                    'WORKLOAD_IDENTITY', 'SECRET')),
            schema_version          text NOT NULL DEFAULT '1',
            mapping_release_id      uuid,
            rate_per_minute         integer NOT NULL DEFAULT 600 CHECK (rate_per_minute > 0),
            cursor_ref              text,
            health                  text NOT NULL DEFAULT 'HEALTHY' CHECK (health IN ('HEALTHY', 'DEGRADED',
                                    'FAILED', 'QUARANTINED')),
            consecutive_failures    integer NOT NULL DEFAULT 0,
            last_success_at         timestamptz,
            last_failure_at         timestamptz,
            freshness_seconds       integer NOT NULL DEFAULT 3600 CHECK (freshness_seconds > 0),
            fresh_until             timestamptz,
            owner_principal_id      uuid NOT NULL,
            disable_behavior        text NOT NULL DEFAULT 'FAIL_CLOSED_HIGH_RISK'
                                    CHECK (disable_behavior IN ('FAIL_CLOSED_HIGH_RISK', 'SUSPEND_DEPENDENTS')),
            quarantine_reason       text,
            revision                bigint NOT NULL DEFAULT 1,
            created_at              timestamptz NOT NULL DEFAULT now(),
            CHECK ((health = 'QUARANTINED') = (quarantine_reason IS NOT NULL))
        );
        CREATE UNIQUE INDEX connector_source_once ON platform.integration_connector (tenant_id, source_ref)
            WHERE source_ref IS NOT NULL;

        CREATE TABLE platform.connector_credential (
            connector_id            uuid NOT NULL REFERENCES platform.integration_connector (connector_id),
            version                 integer NOT NULL CHECK (version >= 1),
            tenant_id               uuid NOT NULL,
            status                  text NOT NULL CHECK (status IN ('ACTIVE', 'RETIRING', 'REVOKED')),
            envelope                bytea NOT NULL,
            fingerprint             text NOT NULL,
            not_after               timestamptz,
            overlap_until           timestamptz,
            alerted_days            integer[] NOT NULL DEFAULT '{}',
            created_by              uuid NOT NULL,
            created_at              timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (connector_id, version),
            CHECK (status <> 'RETIRING' OR overlap_until IS NOT NULL)
        );
        CREATE UNIQUE INDEX connector_credential_one_active ON platform.connector_credential (connector_id)
            WHERE status = 'ACTIVE';
    """)
    for table in TABLES:
        _run(f"""
            ALTER TABLE platform.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE platform.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON platform.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TABLE platform.connector_credential;
        DROP TABLE platform.integration_connector;
        DROP TABLE platform.integration_application;
    """)
