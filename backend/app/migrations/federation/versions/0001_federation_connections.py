"""Federation connections (OIDC and SAML) and federated login transactions.

Revision ID: federation_0001
Revises:
Create Date: 2026-10-13

ZID-AUTH-001 §9, §10, §17, §20, §24 (AUTH-W4/W5).
- authn.federation_connection: one upstream IdP per row, pinned trust material (OIDC JWKS / SAML
  signing certificates) with a metadata expiry, an exact issuer, an approved acr/amr mapping, a
  link policy and a status that can be QUARANTINED at once. Client secrets are never stored: only
  a reference into the secrets broker (DEC-F5). `config_revision` increments on every change.
- authn.federation_login: single-use login transaction (state, nonce, PKCE verifier / SAML request
  id), bound to tenant + connection and, for explicit linking, to the authenticated principal.
"""

from zid_platform.migrations import execute_script as _run

revision = "federation_0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = ("federation_connection", "federation_login")


def upgrade() -> None:
    _run("""
        CREATE TABLE authn.federation_connection (
            connection_id          uuid PRIMARY KEY,
            tenant_id              uuid NOT NULL,
            protocol               text NOT NULL CHECK (protocol IN ('OIDC', 'SAML')),
            display_name           text NOT NULL CHECK (char_length(display_name) <= 128),
            issuer                 text NOT NULL CHECK (char_length(issuer) BETWEEN 1 AND 512),
            status                 text NOT NULL DEFAULT 'ACTIVE'
                                       CHECK (status IN ('ACTIVE', 'QUARANTINED', 'DISABLED')),
            config_revision        integer NOT NULL DEFAULT 1 CHECK (config_revision >= 1),
            -- OIDC
            client_id              text,
            client_auth            text CHECK (client_auth IN ('private_key_jwt', 'client_secret_basic')),
            client_secret_ref      text,
            authorization_endpoint text,
            token_endpoint         text,
            jwks                   jsonb,
            algorithms             text[] NOT NULL DEFAULT '{ES256,RS256}',
            scopes                 text[] NOT NULL DEFAULT '{openid}',
            -- SAML
            sso_url                text,
            idp_certificates       text[],
            sp_entity_id           text,
            allow_idp_initiated    boolean NOT NULL DEFAULT false,
            -- trust and mapping
            metadata_expires_at    timestamptz NOT NULL,
            acr_levels             jsonb NOT NULL DEFAULT '{}',
            trust_amr              boolean NOT NULL DEFAULT true,
            link_policy            text NOT NULL DEFAULT 'EXISTING_ONLY'
                                       CHECK (link_policy IN ('EXISTING_ONLY', 'JIT')),
            created_at             timestamptz NOT NULL DEFAULT now(),
            updated_at             timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, issuer),
            CHECK (protocol <> 'OIDC' OR (client_id IS NOT NULL AND client_auth IS NOT NULL
                   AND authorization_endpoint LIKE 'https://%' AND token_endpoint LIKE 'https://%'
                   AND jsonb_typeof(jwks -> 'keys') = 'array')),
            CHECK (client_auth <> 'client_secret_basic' OR client_secret_ref IS NOT NULL),
            CHECK (protocol <> 'SAML' OR (sso_url LIKE 'https://%' AND cardinality(idp_certificates) >= 1
                   AND sp_entity_id IS NOT NULL)),
            CHECK (algorithms <@ ARRAY['ES256', 'Ed25519', 'RS256', 'PS256']::text[])
        );

        CREATE TABLE authn.federation_login (
            state_hash         bytea PRIMARY KEY CHECK (octet_length(state_hash) = 32),
            tenant_id          uuid NOT NULL,
            connection_id      uuid NOT NULL REFERENCES authn.federation_connection (connection_id),
            config_revision    integer NOT NULL,
            nonce              text,
            code_verifier      text,
            request_id         text,
            client_id          text,
            return_to          text,
            link_principal_id  uuid,
            expires_at         timestamptz NOT NULL,
            used_at            timestamptz,
            created_at         timestamptz NOT NULL DEFAULT now(),
            CHECK (expires_at <= created_at + interval '10 minutes'),
            CHECK (return_to IS NULL OR return_to LIKE '/oauth2/authorize?%')
        );
        CREATE INDEX federation_login_expiry ON authn.federation_login (expires_at);
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
        DROP TABLE authn.federation_login;
        DROP TABLE authn.federation_connection;
    """)
