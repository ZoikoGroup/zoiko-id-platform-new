"""OAuth authorization server state: codes, client directory, session handles, DPoP nonces.

Revision ID: session_0002
Revises: session_0001
Create Date: 2026-10-13

ZID-AUTH-001 §5.1, §6.1-6.4, §7.1, §8 (AUTH-W2).
- authn.client_directory: client_id -> tenant, the one lookup the token and authorize endpoints make
  before a tenant is known (tenant derived from the client registration, never from a header).
  Client ids are therefore globally unique; rows are written only by the oauth_client trigger.
- authn.authorization_code: SHA-256 of the code only; single use, <= 60 s, PKCE S256 challenge
  bound; `family_id` records the refresh family a redemption created so a replay can revoke it.
- authn.session.handle_hash: the browser session handle (cookie) as a hash, set once, never changed
  (no session fixation: a handle is minted only for an authenticated session).
- authn.refresh_token_family: scope and audience it was granted with, so refresh never widens them.
- authn.dpop_nonce: server-issued DPoP nonces for clients that require them (RFC 9449 §8).
"""

from zid_platform.migrations import execute_script as _run

revision = "session_0002"
down_revision = "session_0001"
branch_labels = None
depends_on = None

TENANT_TABLES = ("authorization_code", "dpop_nonce")


def _session_guard(*, handle: bool) -> str:
    handle_check = (
        """
            IF OLD.handle_hash IS NOT NULL AND NEW.handle_hash IS DISTINCT FROM OLD.handle_hash THEN
                RAISE EXCEPTION 'a session handle is set once' USING ERRCODE = 'check_violation';
            END IF;"""
        if handle
        else ""
    )
    return f"""
        CREATE OR REPLACE FUNCTION authn.session_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.session_id, NEW.tenant_id, NEW.principal_id, NEW.auth_time, NEW.authentication_context,
                NEW.aal, NEW.observed_security_revision, NEW.expires_at)
               IS DISTINCT FROM (OLD.session_id, OLD.tenant_id, OLD.principal_id, OLD.auth_time,
                                 OLD.authentication_context, OLD.aal, OLD.observed_security_revision,
                                 OLD.expires_at) THEN
                RAISE EXCEPTION 'session identity and assurance are fixed at issue' USING ERRCODE = 'check_violation';
            END IF;{handle_check}
            IF OLD.status IN ('REVOKED', 'EXPIRED') AND NEW.status IS DISTINCT FROM OLD.status THEN
                RAISE EXCEPTION 'terminal session state' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status = 'REAUTH_REQUIRED' AND NEW.status = 'ACTIVE' THEN
                RAISE EXCEPTION 'a session needing re-authentication is replaced, never reactivated'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
    """


def upgrade() -> None:
    _run("""
        ALTER TABLE authn.session ADD COLUMN handle_hash bytea UNIQUE
            CHECK (handle_hash IS NULL OR octet_length(handle_hash) = 32);

        ALTER TABLE authn.oauth_client
            ADD COLUMN first_party boolean NOT NULL DEFAULT false,
            ADD COLUMN require_dpop_nonce boolean NOT NULL DEFAULT false,
            ADD COLUMN refresh_ttl_seconds integer NOT NULL DEFAULT 1209600
                CHECK (refresh_ttl_seconds BETWEEN 3600 AND 7776000);

        CREATE TABLE authn.client_directory (
            client_id   text PRIMARY KEY,
            tenant_id   uuid NOT NULL
        );
        CREATE FUNCTION authn.oauth_client_directory() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE' AND (NEW.client_id, NEW.tenant_id) IS DISTINCT FROM (OLD.client_id, OLD.tenant_id) THEN
                RAISE EXCEPTION 'client id and tenant are fixed at registration' USING ERRCODE = 'check_violation';
            END IF;
            IF TG_OP = 'INSERT' THEN
                INSERT INTO authn.client_directory (client_id, tenant_id) VALUES (NEW.client_id, NEW.tenant_id);
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER oauth_client_directory BEFORE INSERT OR UPDATE ON authn.oauth_client
            FOR EACH ROW EXECUTE FUNCTION authn.oauth_client_directory();

        CREATE TABLE authn.authorization_code (
            code_hash        bytea PRIMARY KEY CHECK (octet_length(code_hash) = 32),
            tenant_id        uuid NOT NULL,
            client_id        text NOT NULL,
            principal_id     uuid NOT NULL,
            session_id       uuid NOT NULL REFERENCES authn.session (session_id),
            redirect_uri     text NOT NULL,
            scope            text[] NOT NULL DEFAULT '{}',
            audience         text NOT NULL,
            code_challenge   text NOT NULL CHECK (code_challenge ~ '^[A-Za-z0-9_-]{43}$'),
            nonce            text,
            dpop_jkt         text,
            expires_at       timestamptz NOT NULL,
            used_at          timestamptz,
            family_id        uuid REFERENCES authn.refresh_token_family (family_id),
            created_at       timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX authorization_code_expiry ON authn.authorization_code (expires_at);

        ALTER TABLE authn.refresh_token_family
            ADD COLUMN scope text[] NOT NULL DEFAULT '{}',
            ADD COLUMN audience text,
            ADD COLUMN revoked_at timestamptz;
        CREATE INDEX refresh_family_session ON authn.refresh_token_family (session_id, status);

        CREATE TABLE authn.dpop_nonce (
            tenant_id    uuid NOT NULL,
            nonce        text NOT NULL,
            expires_at   timestamptz NOT NULL,
            PRIMARY KEY (tenant_id, nonce)
        );
        CREATE INDEX dpop_nonce_expiry ON authn.dpop_nonce (expires_at);
    """)
    _run(_session_guard(handle=True))
    for table in TENANT_TABLES:
        _run(f"""
            ALTER TABLE authn.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE authn.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON authn.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run(_session_guard(handle=False))
    _run("""
        DROP TABLE authn.dpop_nonce;
        DROP INDEX authn.refresh_family_session;
        ALTER TABLE authn.refresh_token_family DROP COLUMN scope, DROP COLUMN audience, DROP COLUMN revoked_at;
        DROP TABLE authn.authorization_code;
        DROP TRIGGER oauth_client_directory ON authn.oauth_client;
        DROP FUNCTION authn.oauth_client_directory();
        DROP TABLE authn.client_directory;
        ALTER TABLE authn.oauth_client
            DROP COLUMN first_party, DROP COLUMN require_dpop_nonce, DROP COLUMN refresh_ttl_seconds;
        ALTER TABLE authn.session DROP COLUMN handle_hash;
    """)
