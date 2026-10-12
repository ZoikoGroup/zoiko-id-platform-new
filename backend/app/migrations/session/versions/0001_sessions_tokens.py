"""Identity sessions, OAuth clients, refresh-token families and DPoP replay records.

Revision ID: session_0001
Revises:
Create Date: 2026-10-12

ZID-DATA-001 §5.3; ZID-AUTH-001 §5, §6, §7.1, §8.
- authn.session: identity session only (never authority); the security revision it observed is
  fixed at issue, status moves one way (ACTIVE -> REAUTH_REQUIRED -> REVOKED/EXPIRED).
- authn.oauth_client: registered clients; exact redirect URIs (no wildcards), PKCE always required,
  prohibited grants cannot be registered (implicit, password).
- authn.refresh_token_family / authn.refresh_token: only SHA-256 hashes of refresh tokens are stored;
  each generation is used at most once; reuse revokes the family (enforced by the token service).
- authn.dpop_proof_jti: one row per accepted DPoP proof (key thumbprint, jti) inside its window.
"""

from zid_platform.migrations import execute_script as _run

revision = "session_0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "session",
    "oauth_client",
    "refresh_token_family",
    "refresh_token",
    "dpop_proof_jti",
)


def upgrade() -> None:
    _run("""
        CREATE TYPE authn.session_status AS ENUM ('ACTIVE', 'REAUTH_REQUIRED', 'REVOKED', 'EXPIRED');

        CREATE TABLE authn.session (
            session_id                  uuid PRIMARY KEY,
            tenant_id                   uuid NOT NULL,
            principal_id                uuid NOT NULL,
            auth_time                   timestamptz NOT NULL,
            authentication_context      text NOT NULL,
            aal                         smallint NOT NULL CHECK (aal BETWEEN 0 AND 2),
            observed_security_revision  bigint NOT NULL CHECK (observed_security_revision >= 0),
            risk_class                  smallint NOT NULL CHECK (risk_class BETWEEN 0 AND 3),
            status                      authn.session_status NOT NULL DEFAULT 'ACTIVE',
            expires_at                  timestamptz NOT NULL,
            last_seen_at                timestamptz,
            revoked_at                  timestamptz,
            created_at                  timestamptz NOT NULL DEFAULT now(),
            CHECK (expires_at > auth_time)
        );
        CREATE INDEX session_principal ON authn.session (tenant_id, principal_id, status, expires_at);
        CREATE INDEX session_expiry ON authn.session (expires_at);

        CREATE FUNCTION authn.session_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.session_id, NEW.tenant_id, NEW.principal_id, NEW.auth_time, NEW.authentication_context,
                NEW.aal, NEW.observed_security_revision, NEW.expires_at)
               IS DISTINCT FROM (OLD.session_id, OLD.tenant_id, OLD.principal_id, OLD.auth_time,
                                 OLD.authentication_context, OLD.aal, OLD.observed_security_revision,
                                 OLD.expires_at) THEN
                RAISE EXCEPTION 'session identity and assurance are fixed at issue' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status IN ('REVOKED', 'EXPIRED') AND NEW.status IS DISTINCT FROM OLD.status THEN
                RAISE EXCEPTION 'terminal session state' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status = 'REAUTH_REQUIRED' AND NEW.status = 'ACTIVE' THEN
                RAISE EXCEPTION 'a session needing re-authentication is replaced, never reactivated'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER session_guard BEFORE UPDATE ON authn.session
            FOR EACH ROW EXECUTE FUNCTION authn.session_guard();

        CREATE TABLE authn.oauth_client (
            client_id               text NOT NULL,
            tenant_id               uuid NOT NULL,
            client_type             text NOT NULL CHECK (client_type IN ('PUBLIC', 'CONFIDENTIAL', 'SERVICE')),
            profile                 text NOT NULL DEFAULT 'STANDARD' CHECK (profile IN ('STANDARD', 'FAPI2')),
            redirect_uris           text[] NOT NULL DEFAULT '{}',
            grant_types             text[] NOT NULL,
            scopes                  text[] NOT NULL DEFAULT '{}',
            audiences               text[] NOT NULL DEFAULT '{}',
            token_auth_method       text NOT NULL CHECK (token_auth_method IN
                                        ('none', 'private_key_jwt', 'tls_client_auth', 'client_secret_basic')),
            jwks                    jsonb,
            dpop_bound              boolean NOT NULL DEFAULT false,
            status                  text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'SUSPENDED', 'RETIRED')),
            created_at              timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, client_id),
            CHECK (NOT (grant_types && ARRAY['implicit', 'password']::text[])),
            CHECK (array_position(redirect_uris, NULL) IS NULL),
            CHECK (client_type <> 'PUBLIC' OR token_auth_method = 'none'),
            CHECK (profile <> 'FAPI2' OR (dpop_bound OR token_auth_method = 'tls_client_auth'))
        );
        CREATE FUNCTION authn.oauth_client_redirects() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE u text;
        BEGIN
            FOREACH u IN ARRAY NEW.redirect_uris LOOP
                IF u LIKE '%*%' OR u LIKE '%#%' OR NOT (u LIKE 'https://%' OR u LIKE 'http://127.0.0.1%'
                                                        OR u LIKE 'http://[::1]%') THEN
                    RAISE EXCEPTION 'redirect URIs are exact https URIs (loopback http only for native apps)'
                        USING ERRCODE = 'check_violation';
                END IF;
            END LOOP;
            RETURN NEW;
        END $$;
        CREATE TRIGGER oauth_client_redirects BEFORE INSERT OR UPDATE ON authn.oauth_client
            FOR EACH ROW EXECUTE FUNCTION authn.oauth_client_redirects();

        CREATE TABLE authn.refresh_token_family (
            family_id               uuid PRIMARY KEY,
            tenant_id               uuid NOT NULL,
            client_id               text NOT NULL,
            principal_id            uuid NOT NULL,
            session_id              uuid REFERENCES authn.session (session_id),
            sender_key_thumbprint   text,
            current_generation      integer NOT NULL DEFAULT 0 CHECK (current_generation >= 0),
            status                  text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'REVOKED', 'EXPIRED')),
            revoked_reason          text,
            expires_at              timestamptz NOT NULL,
            created_at              timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE authn.refresh_token (
            token_hash              bytea PRIMARY KEY CHECK (octet_length(token_hash) = 32),
            tenant_id               uuid NOT NULL,
            family_id               uuid NOT NULL REFERENCES authn.refresh_token_family (family_id),
            generation              integer NOT NULL CHECK (generation >= 0),
            status                  text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'USED', 'REVOKED', 'EXPIRED')),
            issued_at               timestamptz NOT NULL DEFAULT now(),
            used_at                 timestamptz,
            UNIQUE (family_id, generation)
        );

        CREATE TABLE authn.dpop_proof_jti (
            tenant_id               uuid NOT NULL,
            jkt                     text NOT NULL,
            jti                     text NOT NULL,
            expires_at              timestamptz NOT NULL,
            PRIMARY KEY (tenant_id, jkt, jti)
        );
        CREATE INDEX dpop_proof_expiry ON authn.dpop_proof_jti (expires_at);
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
        DROP TABLE authn.dpop_proof_jti;
        DROP TABLE authn.refresh_token;
        DROP TABLE authn.refresh_token_family;
        DROP TRIGGER oauth_client_redirects ON authn.oauth_client;
        DROP FUNCTION authn.oauth_client_redirects();
        DROP TABLE authn.oauth_client;
        DROP TRIGGER session_guard ON authn.session;
        DROP FUNCTION authn.session_guard();
        DROP TABLE authn.session;
        DROP TYPE authn.session_status;
    """)
