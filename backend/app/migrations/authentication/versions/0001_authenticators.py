"""Authenticators, WebAuthn ceremonies and enrollment tickets.

Revision ID: authentication_0001
Revises:
Create Date: 2026-10-13

ZID-DATA-001 §5.1; ZID-AUTH-001 §3, §4.1, §14 (AUTH-W1).
- authn.authenticator: public key and metadata only (never a private key); the credential id is
  unique per tenant; status follows the §4.1 machine and terminal states never come back.
- authn.webauthn_challenge: SHA-256 of each challenge, bound to tenant, ceremony and (where known)
  principal and session; single use, <= 5 minutes.
- authn.enrollment_ticket: one-time, hashed, short-lived proof of a bootstrap or recovery context
  that allows registering an authenticator without an existing session.
"""

from zid_platform.migrations import execute_script as _run

revision = "authentication_0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = ("authenticator", "webauthn_challenge", "enrollment_ticket")


def upgrade() -> None:
    _run("""
        CREATE TYPE authn.authenticator_status AS ENUM
            ('PENDING_REGISTRATION', 'ACTIVE', 'SUSPENDED', 'REVOKED', 'LOST', 'REPLACED');

        CREATE TABLE authn.authenticator (
            authenticator_id      uuid PRIMARY KEY,
            tenant_id             uuid NOT NULL,
            principal_id          uuid NOT NULL,
            authenticator_type    text NOT NULL CHECK (authenticator_type IN
                                      ('PASSKEY', 'SECURITY_KEY', 'TOTP', 'RECOVERY', 'OTHER')),
            credential_id         bytea CHECK (credential_id IS NULL OR octet_length(credential_id) BETWEEN 16 AND 1023),
            credential_id_hash    bytea CHECK (credential_id_hash IS NULL OR octet_length(credential_id_hash) = 32),
            public_key_cose       bytea,
            aaguid                uuid,
            sign_count            bigint NOT NULL DEFAULT 0 CHECK (sign_count >= 0),
            user_verified         boolean NOT NULL DEFAULT false,
            backup_eligible       boolean NOT NULL DEFAULT false,
            backed_up             boolean NOT NULL DEFAULT false,
            transports            text[] NOT NULL DEFAULT '{}',
            label                 text CHECK (label IS NULL OR char_length(label) <= 64),
            status                authn.authenticator_status NOT NULL DEFAULT 'ACTIVE',
            assurance_level       text,
            registered_at         timestamptz NOT NULL,
            last_used_at          timestamptz,
            revoked_at            timestamptz,
            revoked_reason        text,
            CHECK (authenticator_type NOT IN ('PASSKEY', 'SECURITY_KEY')
                   OR (credential_id IS NOT NULL AND credential_id_hash IS NOT NULL AND public_key_cose IS NOT NULL))
        );
        CREATE UNIQUE INDEX authenticator_credential ON authn.authenticator (tenant_id, credential_id_hash);
        CREATE INDEX authenticator_principal ON authn.authenticator (tenant_id, principal_id, status);

        CREATE FUNCTION authn.authenticator_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.authenticator_id, NEW.tenant_id, NEW.principal_id, NEW.authenticator_type,
                NEW.credential_id, NEW.credential_id_hash, NEW.public_key_cose, NEW.registered_at)
               IS DISTINCT FROM (OLD.authenticator_id, OLD.tenant_id, OLD.principal_id, OLD.authenticator_type,
                                 OLD.credential_id, OLD.credential_id_hash, OLD.public_key_cose, OLD.registered_at) THEN
                RAISE EXCEPTION 'an authenticator is replaced by a new record, never rewritten'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
                (OLD.status = 'PENDING_REGISTRATION' AND NEW.status IN ('ACTIVE', 'REVOKED'))
                OR (OLD.status = 'ACTIVE' AND NEW.status IN ('SUSPENDED', 'REVOKED', 'LOST', 'REPLACED'))
                OR (OLD.status = 'SUSPENDED' AND NEW.status IN ('REVOKED', 'LOST', 'REPLACED'))
            ) THEN
                RAISE EXCEPTION 'authenticator status % -> % is not allowed', OLD.status, NEW.status
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.sign_count < OLD.sign_count THEN
                RAISE EXCEPTION 'sign_count never moves backwards' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER authenticator_guard BEFORE UPDATE ON authn.authenticator
            FOR EACH ROW EXECUTE FUNCTION authn.authenticator_guard();

        CREATE TABLE authn.webauthn_challenge (
            challenge_hash   bytea PRIMARY KEY CHECK (octet_length(challenge_hash) = 32),
            tenant_id        uuid NOT NULL,
            ceremony         text NOT NULL CHECK (ceremony IN ('REGISTRATION', 'AUTHENTICATION', 'STEP_UP')),
            principal_id     uuid,
            session_id       uuid,
            ticket_hash      bytea,
            user_verification text NOT NULL CHECK (user_verification IN ('required', 'preferred')),
            expires_at       timestamptz NOT NULL,
            used_at          timestamptz,
            created_at       timestamptz NOT NULL DEFAULT now(),
            CHECK (expires_at <= created_at + interval '5 minutes'),
            CHECK (ceremony = 'AUTHENTICATION' OR principal_id IS NOT NULL)
        );
        CREATE INDEX webauthn_challenge_expiry ON authn.webauthn_challenge (expires_at);

        CREATE TABLE authn.enrollment_ticket (
            ticket_hash      bytea PRIMARY KEY CHECK (octet_length(ticket_hash) = 32),
            tenant_id        uuid NOT NULL,
            principal_id     uuid NOT NULL,
            purpose          text NOT NULL CHECK (purpose IN ('BOOTSTRAP', 'RECOVERY')),
            issued_by        text NOT NULL,
            recovery_id      uuid,
            expires_at       timestamptz NOT NULL,
            used_at          timestamptz,
            created_at       timestamptz NOT NULL DEFAULT now(),
            CHECK (expires_at <= created_at + interval '24 hours'),
            CHECK (purpose <> 'RECOVERY' OR recovery_id IS NOT NULL)
        );
    """)
    for table in TENANT_TABLES:
        _run(f"""
            ALTER TABLE authn.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE authn.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON authn.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TABLE authn.enrollment_ticket;
        DROP TABLE authn.webauthn_challenge;
        DROP TRIGGER authenticator_guard ON authn.authenticator;
        DROP FUNCTION authn.authenticator_guard();
        DROP TABLE authn.authenticator;
        DROP TYPE authn.authenticator_status;
    """)
