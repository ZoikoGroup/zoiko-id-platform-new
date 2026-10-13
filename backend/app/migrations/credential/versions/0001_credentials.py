"""Verifiable credentials: configurations, issuer trust, RP registration, presentations, issuance.

Revision ID: credential_0001
Revises:
Create Date: 2026-10-13

ZID-DATA-001 §6.3; ZID-VC-001 §2-5, §7, §9-15, §18; DEC-E2 (verifier first, controlled issuer).
- cred.credential_configuration: immutable-once-ACTIVE type/format/claims/binding/status profile,
  with the pinned adapter version (SD-JWT VC and Token Status List are not final RFCs).
- cred.issuer_trust: typed trust - an issuer is trusted for named credential types and
  jurisdictions only, through pinned keys or an OpenID Federation anchor; unknown = untrusted.
- cred.rp_registration: purposes and the attributes each purpose may request (minimization).
- cred.presentation_record: one OID4VP transaction (request, nonce/state as hashes, outcome,
  claim commitments only - raw presentations are not stored).
- cred.credential_record: credentials Zoiko issued (minimal metadata + status-list index).
- cred.status_list: Zoiko's own Token Status Lists for credentials it issued.
"""

from zid_platform.migrations import execute_script as _run

revision = "credential_0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "credential_configuration",
    "issuer_trust",
    "rp_registration",
    "presentation_record",
    "credential_record",
    "status_list",
)


def upgrade() -> None:
    _run("""
        CREATE TABLE cred.credential_configuration (
            configuration_id        uuid PRIMARY KEY,
            tenant_id               uuid NOT NULL,
            credential_type         text NOT NULL,
            format                  text NOT NULL CHECK (format IN ('SD_JWT_VC', 'MDOC')),
            adapter_version         text NOT NULL,
            required_claims         text[] NOT NULL,
            optional_claims         text[] NOT NULL DEFAULT '{}',
            holder_binding_required boolean NOT NULL DEFAULT true,
            status_method           text NOT NULL CHECK (status_method IN ('TOKEN_STATUS_LIST', 'NONE')),
            status_mandatory        boolean NOT NULL DEFAULT true,
            risk_class              smallint NOT NULL DEFAULT 2 CHECK (risk_class BETWEEN 0 AND 3),
            assurance_profile_id    uuid,
            issuance_enabled        boolean NOT NULL DEFAULT false,
            status                  text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'ACTIVE', 'RETIRED', 'REVOKED')),
            created_at              timestamptz NOT NULL DEFAULT now(),
            CHECK (risk_class < 2 OR status_method <> 'NONE' OR NOT status_mandatory)
        );
        CREATE FUNCTION cred.configuration_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status <> 'DRAFT' AND (NEW.credential_type, NEW.format, NEW.adapter_version, NEW.required_claims,
                    NEW.optional_claims, NEW.holder_binding_required, NEW.status_method, NEW.status_mandatory)
                IS DISTINCT FROM (OLD.credential_type, OLD.format, OLD.adapter_version, OLD.required_claims,
                    OLD.optional_claims, OLD.holder_binding_required, OLD.status_method, OLD.status_mandatory) THEN
                RAISE EXCEPTION 'an active credential configuration is immutable' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status IN ('RETIRED', 'REVOKED') AND NEW.status IS DISTINCT FROM OLD.status THEN
                RAISE EXCEPTION 'terminal configuration state' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER configuration_guard BEFORE UPDATE ON cred.credential_configuration
            FOR EACH ROW EXECUTE FUNCTION cred.configuration_guard();

        CREATE TABLE cred.issuer_trust (
            trust_id            uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            issuer_identifier   text NOT NULL,
            credential_types    text[] NOT NULL CHECK (cardinality(credential_types) >= 1),
            jurisdictions       text[] NOT NULL,
            trust_source        text NOT NULL CHECK (trust_source IN ('PINNED', 'FEDERATION')),
            jwks                jsonb,
            iaca_certificates   text[],
            trust_anchor_id     uuid,
            algorithms          text[] NOT NULL DEFAULT '{ES256}',
            status              text NOT NULL DEFAULT 'ACTIVE'
                                    CHECK (status IN ('ACTIVE', 'SUSPENDED', 'REVOKED', 'QUARANTINED')),
            valid_to            timestamptz NOT NULL,
            revision            integer NOT NULL DEFAULT 1,
            created_at          timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, issuer_identifier),
            CHECK (trust_source <> 'PINNED' OR jwks IS NOT NULL OR iaca_certificates IS NOT NULL),
            CHECK (trust_source <> 'FEDERATION' OR trust_anchor_id IS NOT NULL)
        );

        CREATE TABLE cred.rp_registration (
            registration_id     uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            client_id           text NOT NULL,
            purpose             text NOT NULL,
            allowed_attributes  text[] NOT NULL,
            retention_days      integer NOT NULL DEFAULT 0 CHECK (retention_days BETWEEN 0 AND 3650),
            status              text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'SUSPENDED', 'REVOKED', 'EXPIRED')),
            valid_to            timestamptz NOT NULL,
            UNIQUE (tenant_id, client_id, purpose)
        );

        CREATE TABLE cred.presentation_record (
            presentation_id     uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            configuration_id    uuid NOT NULL REFERENCES cred.credential_configuration (configuration_id),
            relying_client_id   text NOT NULL,
            purpose             text NOT NULL,
            requested_claims    text[] NOT NULL,
            subject_principal_id uuid,
            jurisdiction        text NOT NULL CHECK (jurisdiction ~ '^[A-Z]{2}$'),
            nonce               text NOT NULL,
            state_hash          bytea NOT NULL UNIQUE CHECK (octet_length(state_hash) = 32),
            status              text NOT NULL CHECK (status IN ('REQUESTED', 'VERIFIED', 'REJECTED', 'EXPIRED')),
            reason_codes        text[] NOT NULL DEFAULT '{}',
            issuer_identifier   text,
            credential_status   text,
            claim_commitments   jsonb,
            disclosed_claims    jsonb,
            request_jwt         text NOT NULL,
            request_fetched_at  timestamptz,
            created_at          timestamptz NOT NULL,
            expires_at          timestamptz NOT NULL,
            completed_at        timestamptz
        );
        CREATE FUNCTION cred.presentation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status <> 'REQUESTED' AND NEW.status IS DISTINCT FROM OLD.status THEN
                RAISE EXCEPTION 'a presentation transaction is answered once' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER presentation_guard BEFORE UPDATE ON cred.presentation_record
            FOR EACH ROW EXECUTE FUNCTION cred.presentation_guard();

        CREATE TABLE cred.status_list (
            list_id        uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            bits           smallint NOT NULL DEFAULT 2 CHECK (bits IN (1, 2, 4, 8)),
            size           integer NOT NULL CHECK (size BETWEEN 1024 AND 1048576),
            next_index     integer NOT NULL DEFAULT 0,
            statuses       bytea NOT NULL,
            revision       integer NOT NULL DEFAULT 1,
            created_at     timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE cred.credential_record (
            credential_id        uuid PRIMARY KEY,
            tenant_id            uuid NOT NULL,
            subject_principal_id uuid,
            issuer_identifier    text NOT NULL,
            credential_type      text NOT NULL,
            format               text NOT NULL CHECK (format IN ('VC', 'SD-JWT', 'MDOC', 'OTHER')),
            configuration_id     uuid NOT NULL REFERENCES cred.credential_configuration (configuration_id),
            status               text NOT NULL CHECK (status IN ('VALID', 'SUSPENDED', 'REVOKED', 'EXPIRED', 'UNKNOWN')),
            status_ref           text,
            status_list_id       uuid REFERENCES cred.status_list (list_id),
            status_index         integer,
            holder_key_thumbprint text,
            issuance_transaction text NOT NULL,
            issued_at            timestamptz NOT NULL,
            expires_at           timestamptz,
            UNIQUE (tenant_id, issuance_transaction)
        );
    """)
    for table in TENANT_TABLES:
        _run(f"""
            ALTER TABLE cred.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE cred.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON cred.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TABLE cred.credential_record;
        DROP TABLE cred.status_list;
        DROP TRIGGER presentation_guard ON cred.presentation_record;
        DROP FUNCTION cred.presentation_guard();
        DROP TABLE cred.presentation_record;
        DROP TABLE cred.rp_registration;
        DROP TABLE cred.issuer_trust;
        DROP TRIGGER configuration_guard ON cred.credential_configuration;
        DROP FUNCTION cred.configuration_guard();
        DROP TABLE cred.credential_configuration;
    """)
