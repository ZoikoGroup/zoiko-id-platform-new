"""Official-register attestations of a provider's certification (ZID-RCG-003 RCG-G02, section 05; CR-073).

Revision ID: verification_0007
Revises: verification_0006
Create Date: 2026-11-12

certification_attestation: one row per reading of an official register, append-only. It names the register entry, when it was read, the hash of
what it returned and where the preserved response is kept; the response itself is not stored here. The latest row for a scope says what is true
now, and an earlier row still answers for a past moment, so a later withdrawal never rewrites a check made before it. Platform configuration, not
tenant data: no tenant_id, no row-level security. The table repeats the domain rules: no update, no delete, never from the future, and each
snapshot for a scope newer than the one before it.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0007"
down_revision = "verification_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.certification_attestation (
            snapshot_id       text PRIMARY KEY CHECK (length(snapshot_id) BETWEEN 1 AND 128),
            entity            text NOT NULL CHECK (length(entity) BETWEEN 1 AND 128),
            scheme            text NOT NULL CHECK (length(scheme) BETWEEN 1 AND 64),
            country           text NOT NULL CHECK (country ~ '^[A-Z]{2}$'),
            service_code      text NOT NULL CHECK (length(service_code) BETWEEN 1 AND 64),
            scheme_version    text NOT NULL CHECK (length(scheme_version) BETWEEN 1 AND 64),
            certificate_ref   text NOT NULL CHECK (length(certificate_ref) BETWEEN 1 AND 256),
            component_only    boolean NOT NULL,
            status            text NOT NULL CHECK (status IN ('ACTIVE', 'SUSPENDED', 'WITHDRAWN', 'EXPIRED')),
            valid_from        date NOT NULL,
            valid_until       date,
            register_url      text NOT NULL CHECK (register_url ~ '^https://[^[:space:]/]+'),
            register_entry_id text NOT NULL CHECK (length(btrim(register_entry_id)) BETWEEN 1 AND 256),
            cab               text CHECK (cab IS NULL OR length(cab) BETWEEN 1 AND 128),
            fetched_at        timestamptz NOT NULL,
            proof_hash        text NOT NULL CHECK (proof_hash ~ '^[0-9a-f]{64}$'),
            evidence_locator  text NOT NULL CHECK (evidence_locator ~ '^[A-Za-z0-9][A-Za-z0-9._:/#@+=-]{2,255}$'),
            recorded_by       text NOT NULL CHECK (length(btrim(recorded_by)) BETWEEN 1 AND 128),
            recorded_at       timestamptz NOT NULL,
            CHECK (valid_until IS NULL OR valid_until >= valid_from)
        );
        CREATE INDEX certification_attestation_scope
            ON verify.certification_attestation (entity, scheme, country, service_code, fetched_at DESC);

        CREATE FUNCTION verify.certification_attestation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'certification attestations are append-only' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.fetched_at > now() + interval '5 minutes' THEN
                RAISE EXCEPTION 'an attestation cannot be from the future' USING ERRCODE = 'check_violation';
            END IF;
            IF EXISTS (SELECT 1 FROM verify.certification_attestation a
                       WHERE a.entity = NEW.entity AND a.scheme = NEW.scheme AND a.country = NEW.country
                         AND a.service_code = NEW.service_code AND a.fetched_at >= NEW.fetched_at) THEN
                RAISE EXCEPTION 'an attestation must be newer than the one before it for the same scope' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER certification_attestation_guard BEFORE INSERT OR UPDATE OR DELETE ON verify.certification_attestation
            FOR EACH ROW EXECUTE FUNCTION verify.certification_attestation_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.certification_attestation;
        DROP FUNCTION verify.certification_attestation_guard();
    """)
