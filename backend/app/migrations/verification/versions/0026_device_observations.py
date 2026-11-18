"""WF-05 device observations and attestation nonces (ZID-DTV-WF-05 DTV-22, §06 DeviceObservation; QA-030 to QA-034; CR-116).

Revision ID: verification_0026
Revises: verification_0025
Create Date: 2026-11-18

check_device_nonce: a server nonce for one platform attestation, bound to a case and session; only its keyed hash is kept; used once.
check_device_observation: one observation: a pseudonym keyed by tenant and rotation period (never the raw device reference), the platform, the
attestation status, the named signals and the evaluation. Append-only. Both tables are tenant data under row-level security.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0026"
down_revision = "verification_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.check_device_nonce (
            nonce_id     uuid PRIMARY KEY,
            tenant_id    uuid NOT NULL,
            case_id      uuid NOT NULL REFERENCES verify.check_case (case_id),
            session_ref  text NOT NULL CHECK (length(session_ref) BETWEEN 1 AND 128),
            nonce_hash   bytea NOT NULL UNIQUE CHECK (octet_length(nonce_hash) = 32),
            expires_at   timestamptz NOT NULL,
            created_at   timestamptz NOT NULL,
            consumed_at  timestamptz,
            CHECK (expires_at > created_at AND expires_at <= created_at + interval '5 minutes')
        );

        CREATE FUNCTION verify.check_device_nonce_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'nonces are kept' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.consumed_at IS NOT NULL OR NEW.consumed_at IS NULL
               OR (NEW.nonce_id, NEW.tenant_id, NEW.case_id, NEW.session_ref, NEW.nonce_hash, NEW.expires_at, NEW.created_at)
                  IS DISTINCT FROM (OLD.nonce_id, OLD.tenant_id, OLD.case_id, OLD.session_ref, OLD.nonce_hash, OLD.expires_at, OLD.created_at) THEN
                RAISE EXCEPTION 'a nonce is consumed once and otherwise never changes' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER check_device_nonce_guard BEFORE UPDATE OR DELETE ON verify.check_device_nonce
            FOR EACH ROW EXECUTE FUNCTION verify.check_device_nonce_guard();

        CREATE TABLE verify.check_device_observation (
            observation_id uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            case_id        uuid NOT NULL REFERENCES verify.check_case (case_id),
            pseudonym      text NOT NULL CHECK (pseudonym ~ '^dp1\\.[0-9]+\\.[0-9a-f]{40}$'),
            platform       text NOT NULL CHECK (platform IN ('WEB', 'ANDROID', 'IOS')),
            attestation    text NOT NULL CHECK (attestation IN ('VALID', 'INVALID', 'UNAVAILABLE', 'NOT_REQUESTED')),
            signals        text[] NOT NULL,
            verdict        text NOT NULL CHECK (verdict IN ('PASS', 'REVIEW', 'INDETERMINATE')),
            reasons        text[] NOT NULL,
            observed_at    timestamptz NOT NULL
        );
        CREATE INDEX check_device_observation_case ON verify.check_device_observation (tenant_id, case_id, observed_at DESC);

        CREATE FUNCTION verify.check_device_observation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'device observations are append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER check_device_observation_guard BEFORE UPDATE OR DELETE ON verify.check_device_observation
            FOR EACH ROW EXECUTE FUNCTION verify.check_device_observation_guard();

        ALTER TABLE verify.check_device_nonce ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.check_device_nonce FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.check_device_nonce
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        ALTER TABLE verify.check_device_observation ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.check_device_observation FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.check_device_observation
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.check_device_observation;
        DROP FUNCTION verify.check_device_observation_guard();
        DROP TABLE verify.check_device_nonce;
        DROP FUNCTION verify.check_device_nonce_guard();
    """)
