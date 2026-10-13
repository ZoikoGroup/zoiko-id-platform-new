"""Identity verification: profiles, adapters, sessions, assertions, review, reliance.

Revision ID: verification_0001
Revises:
Create Date: 2026-10-13

ZID-DATA-001 §6.1-6.2; ZID-VERIFY-001 §3-5, §7, §9-10, §16-17, §22.
- verify.assurance_profile: versioned verification profile (IAL target, required claims, evidence,
  binding, biometric gating, freshness, providers, review and reliance policy). Immutable once
  ACTIVE; a change is a new version. Tenant-scoped in this release (platform profiles: CR-009).
- verify.provider_adapter: one provider integration: pinned callback keys, status/reason mapping
  version, equivalence group, circuit-breaker state; QUARANTINED stops it at once.
- verify.verification_session: the §5 state machine; terminal states never change.
- verify.verification_assertion: one immutable row per claim decision (value as a commitment
  only); status may only move VALID -> STALE/EXPIRED/REVOKED.
- verify.provider_callback: (adapter, provider event) dedupe with content hash.
- verify.review_case / verify.review_decision: reason-coded manual review, one decision per
  reviewer, dual control where the profile requires it.
- verify.reliance_decision: explicit reuse decisions (REUSE_ALLOWED / REVERIFY_REQUIRED / ...).
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "assurance_profile",
    "provider_adapter",
    "verification_session",
    "verification_assertion",
    "provider_callback",
    "review_case",
    "review_decision",
    "reliance_decision",
)


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.assurance_profile (
            assurance_profile_id  uuid PRIMARY KEY,
            tenant_id             uuid NOT NULL,
            profile_name          text NOT NULL,
            profile_version       integer NOT NULL CHECK (profile_version >= 1),
            dimension             text NOT NULL DEFAULT 'IDENTITY'
                                      CHECK (dimension IN ('IDENTITY', 'AUTHENTICATION', 'FEDERATION', 'CREDENTIAL', 'AUTHORITY')),
            level                 text NOT NULL CHECK (level IN ('IAL-Z1', 'IAL-Z2', 'IAL-Z3')),
            requirements_json     jsonb NOT NULL,
            status                text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'ACTIVE', 'RETIRED')),
            effective_at          timestamptz,
            created_at            timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, profile_name, profile_version)
        );
        CREATE FUNCTION verify.profile_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status <> 'DRAFT' AND (NEW.level, NEW.requirements_json, NEW.profile_name, NEW.profile_version)
                   IS DISTINCT FROM (OLD.level, OLD.requirements_json, OLD.profile_name, OLD.profile_version) THEN
                RAISE EXCEPTION 'an active profile version is immutable' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status = 'RETIRED' AND NEW.status <> 'RETIRED' THEN
                RAISE EXCEPTION 'a retired profile stays retired' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status = 'ACTIVE' AND NEW.status = 'DRAFT' THEN
                RAISE EXCEPTION 'an active profile cannot return to draft' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER profile_guard BEFORE UPDATE ON verify.assurance_profile
            FOR EACH ROW EXECUTE FUNCTION verify.profile_guard();

        CREATE TABLE verify.provider_adapter (
            adapter_id            text NOT NULL,
            tenant_id             uuid NOT NULL,
            provider              text NOT NULL,
            mapping_version       text NOT NULL,
            submit_endpoint       text NOT NULL CHECK (submit_endpoint LIKE 'https://%'),
            callback_jwks         jsonb NOT NULL CHECK (jsonb_typeof(callback_jwks -> 'keys') = 'array'),
            callback_algorithms   text[] NOT NULL DEFAULT '{ES256}',
            equivalence_group     text NOT NULL,
            supported_evidence    text[] NOT NULL,
            jurisdictions         text[] NOT NULL,
            status                text NOT NULL DEFAULT 'ACTIVE'
                                      CHECK (status IN ('ACTIVE', 'DEGRADED', 'QUARANTINED', 'RETIRED')),
            consecutive_failures  integer NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
            open_until            timestamptz,
            created_at            timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, adapter_id)
        );

        CREATE TABLE verify.verification_session (
            session_id            uuid PRIMARY KEY,
            tenant_id             uuid NOT NULL,
            assurance_profile_id  uuid NOT NULL REFERENCES verify.assurance_profile (assurance_profile_id),
            subject_principal_id  uuid NOT NULL,
            purpose               text NOT NULL,
            jurisdiction          text NOT NULL,
            status                text NOT NULL CHECK (status IN ('CREATED', 'IN_PROGRESS', 'PENDING_EXTERNAL',
                                      'MANUAL_REVIEW', 'VERIFIED', 'FAILED', 'CANCELED', 'EXPIRED')),
            adapter_id            text,
            provider_transaction_ref text,
            attempted_adapters    text[] NOT NULL DEFAULT '{}',
            outcome_code          text,
            pending_result        jsonb,
            supersedes_session_id uuid,
            row_version           integer NOT NULL DEFAULT 1,
            created_at            timestamptz NOT NULL,
            updated_at            timestamptz NOT NULL,
            expires_at            timestamptz NOT NULL
        );
        CREATE UNIQUE INDEX verification_session_provider_ref
            ON verify.verification_session (tenant_id, adapter_id, provider_transaction_ref)
            WHERE provider_transaction_ref IS NOT NULL;
        CREATE FUNCTION verify.session_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status IN ('VERIFIED', 'FAILED', 'CANCELED', 'EXPIRED') AND NEW.status IS DISTINCT FROM OLD.status THEN
                RAISE EXCEPTION 'terminal verification state' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.subject_principal_id, NEW.assurance_profile_id, NEW.tenant_id)
               IS DISTINCT FROM (OLD.subject_principal_id, OLD.assurance_profile_id, OLD.tenant_id) THEN
                RAISE EXCEPTION 'verification subject and profile are fixed' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER session_guard BEFORE UPDATE ON verify.verification_session
            FOR EACH ROW EXECUTE FUNCTION verify.session_guard();

        CREATE TABLE verify.verification_assertion (
            assertion_id            uuid PRIMARY KEY,
            tenant_id               uuid NOT NULL,
            subject_principal_id    uuid NOT NULL,
            session_id              uuid NOT NULL REFERENCES verify.verification_session (session_id),
            claim_type              text NOT NULL,
            claim_value_commitment  bytea NOT NULL CHECK (octet_length(claim_value_commitment) = 32),
            verification_method     text NOT NULL,
            evidence_types          text[] NOT NULL DEFAULT '{}',
            provider_id             text NOT NULL,
            provider_reference      text,
            assurance_profile_id    uuid NOT NULL REFERENCES verify.assurance_profile (assurance_profile_id),
            assurance_level         text NOT NULL,
            jurisdiction            text,
            evidence_commitment     bytea,
            status                  text NOT NULL CHECK (status IN ('VALID', 'STALE', 'REVOKED', 'EXPIRED', 'FAILED')),
            reason_codes            text[] NOT NULL DEFAULT '{}',
            performed_at            timestamptz NOT NULL,
            valid_until             timestamptz,
            revoked_at              timestamptz
        );
        CREATE INDEX verification_assertion_subject
            ON verify.verification_assertion (subject_principal_id, claim_type, status, valid_until);
        CREATE FUNCTION verify.assertion_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.assertion_id, NEW.subject_principal_id, NEW.claim_type, NEW.claim_value_commitment,
                NEW.verification_method, NEW.assurance_level, NEW.performed_at, NEW.valid_until)
               IS DISTINCT FROM (OLD.assertion_id, OLD.subject_principal_id, OLD.claim_type,
                                 OLD.claim_value_commitment, OLD.verification_method, OLD.assurance_level,
                                 OLD.performed_at, OLD.valid_until) THEN
                RAISE EXCEPTION 'assertions are immutable; new results supersede' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status
               AND NOT (OLD.status = 'VALID' AND NEW.status IN ('STALE', 'EXPIRED', 'REVOKED')) THEN
                RAISE EXCEPTION 'assertion status % -> % not allowed', OLD.status, NEW.status
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER assertion_guard BEFORE UPDATE ON verify.verification_assertion
            FOR EACH ROW EXECUTE FUNCTION verify.assertion_guard();

        CREATE TABLE verify.provider_callback (
            tenant_id           uuid NOT NULL,
            adapter_id          text NOT NULL,
            provider_event_id   text NOT NULL,
            content_hash        bytea NOT NULL CHECK (octet_length(content_hash) = 32),
            received_at         timestamptz NOT NULL DEFAULT now(),
            outcome             text NOT NULL,
            PRIMARY KEY (tenant_id, adapter_id, provider_event_id)
        );

        CREATE TABLE verify.review_case (
            case_id             uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            session_id          uuid NOT NULL UNIQUE REFERENCES verify.verification_session (session_id),
            required_approvals  smallint NOT NULL CHECK (required_approvals BETWEEN 1 AND 3),
            status              text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'APPROVED', 'REJECTED', 'ESCALATED')),
            reason_codes        text[] NOT NULL DEFAULT '{}',
            created_at          timestamptz NOT NULL DEFAULT now(),
            closed_at           timestamptz
        );
        CREATE TABLE verify.review_decision (
            case_id             uuid NOT NULL REFERENCES verify.review_case (case_id),
            tenant_id           uuid NOT NULL,
            reviewer_principal_id uuid NOT NULL,
            decision            text NOT NULL CHECK (decision IN ('APPROVE', 'REJECT', 'REQUEST_MORE_EVIDENCE', 'ESCALATE')),
            reason_codes        text[] NOT NULL CHECK (cardinality(reason_codes) >= 1),
            decided_at          timestamptz NOT NULL,
            PRIMARY KEY (case_id, reviewer_principal_id)
        );

        CREATE TABLE verify.reliance_decision (
            reliance_decision_id uuid PRIMARY KEY,
            tenant_id            uuid NOT NULL,
            subject_principal_id uuid NOT NULL,
            relying_client_id    text NOT NULL,
            purpose              text NOT NULL,
            required_level       text NOT NULL,
            jurisdiction         text,
            assertion_ids        uuid[] NOT NULL,
            outcome              text NOT NULL CHECK (outcome IN ('REUSE_ALLOWED', 'REVERIFY_REQUIRED',
                                     'SUPPLEMENT_REQUIRED', 'DENY')),
            reason_codes         text[] NOT NULL,
            decided_at           timestamptz NOT NULL
        );
    """)
    for table in TENANT_TABLES:
        _run(f"""
            ALTER TABLE verify.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE verify.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON verify.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.reliance_decision;
        DROP TABLE verify.review_decision;
        DROP TABLE verify.review_case;
        DROP TABLE verify.provider_callback;
        DROP TRIGGER assertion_guard ON verify.verification_assertion;
        DROP FUNCTION verify.assertion_guard();
        DROP TABLE verify.verification_assertion;
        DROP TRIGGER session_guard ON verify.verification_session;
        DROP FUNCTION verify.session_guard();
        DROP TABLE verify.verification_session;
        DROP TABLE verify.provider_adapter;
        DROP TRIGGER profile_guard ON verify.assurance_profile;
        DROP FUNCTION verify.profile_guard();
        DROP TABLE verify.assurance_profile;
    """)
