"""Confidential clients, pushed authorization requests (AUTH-W3).

Revision ID: session_0003
Revises: session_0002
Create Date: 2026-10-13

ZID-AUTH-001 §6.1-6.2, §7, §13; RFC 7523, 8705, 9126; FAPI 2.0 Security Profile.
- authn.oauth_client: `tls_subject_dn` for tls_client_auth, `service_principal_id` (the subject of
  client_credentials tokens: a registered principal, never a bare client id), `require_par`.
  A FAPI 2.0 client must be confidential, authenticate asymmetrically and use PAR.
  private_key_jwt clients must register public keys; tls_client_auth clients a subject DN.
- authn.par_request: SHA-256 of the request_uri, the validated parameters, single use, <= 90 s.
"""

from zid_platform.migrations import execute_script as _run

revision = "session_0003"
down_revision = "session_0002"
branch_labels = None
depends_on = None

TENANT_TABLES = ("par_request",)


def upgrade() -> None:
    _run("""
        ALTER TABLE authn.oauth_client
            ADD COLUMN tls_subject_dn text,
            ADD COLUMN service_principal_id uuid,
            ADD COLUMN require_par boolean NOT NULL DEFAULT false,
            ADD CONSTRAINT oauth_client_fapi2 CHECK (profile <> 'FAPI2' OR (
                client_type <> 'PUBLIC'
                AND token_auth_method IN ('private_key_jwt', 'tls_client_auth')
                AND require_par)),
            ADD CONSTRAINT oauth_client_private_key_jwt CHECK (
                token_auth_method <> 'private_key_jwt' OR jsonb_typeof(jwks -> 'keys') = 'array'),
            ADD CONSTRAINT oauth_client_tls CHECK (
                token_auth_method <> 'tls_client_auth' OR tls_subject_dn IS NOT NULL),
            ADD CONSTRAINT oauth_client_secret_exception CHECK (token_auth_method <> 'client_secret_basic');

        CREATE TABLE authn.par_request (
            request_uri_hash  bytea PRIMARY KEY CHECK (octet_length(request_uri_hash) = 32),
            tenant_id         uuid NOT NULL,
            client_id         text NOT NULL,
            params            jsonb NOT NULL,
            dpop_jkt          text,
            expires_at        timestamptz NOT NULL,
            used_at           timestamptz,
            created_at        timestamptz NOT NULL DEFAULT now(),
            CHECK (expires_at <= created_at + interval '90 seconds')
        );
        CREATE INDEX par_request_expiry ON authn.par_request (expires_at);
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
        DROP TABLE authn.par_request;
        ALTER TABLE authn.oauth_client
            DROP CONSTRAINT oauth_client_secret_exception,
            DROP CONSTRAINT oauth_client_tls,
            DROP CONSTRAINT oauth_client_private_key_jwt,
            DROP CONSTRAINT oauth_client_fapi2,
            DROP COLUMN require_par,
            DROP COLUMN service_principal_id,
            DROP COLUMN tls_subject_dn;
    """)
