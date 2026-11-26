"""Stronger credential options: private-key JWT credentials and an IP allowlist (ZID-EXT-IDV-001 WF-05, SEC-02; CR-158).

Revision ID: verify_api_0007
Revises: verify_api_0006
Create Date: 2026-11-25

A credential is now one of two kinds. SECRET is the original client id and a secret kept as a hash. PRIVATE_KEY_JWT holds only the company's PUBLIC key: the company's server proves itself by signing a short
assertion with its own private key (RFC 7523), so Zoiko iD never holds anything that could be leaked and used to call the API. `api_assertion_jti` remembers each assertion id until it expires, so a signed assertion
can be used once and a copy captured in transit is useless.

`ip_allowlist` on an application restricts which networks its server may call from (empty means no restriction).
"""

from zid_platform.migrations import execute_script as _run

revision = "verify_api_0007"
down_revision = "verify_api_0006"
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
AUTH = "current_setting('app.verify_api_scope', true) = 'auth'"


def upgrade() -> None:
    _run(f"""
        ALTER TABLE verify.api_application ADD COLUMN ip_allowlist text[] NOT NULL DEFAULT '{{}}';

        ALTER TABLE verify.api_credential
            ADD COLUMN auth_method text NOT NULL DEFAULT 'SECRET' CHECK (auth_method IN ('SECRET', 'PRIVATE_KEY_JWT')),
            ADD COLUMN public_jwk jsonb,
            ALTER COLUMN secret_hash DROP NOT NULL,
            ALTER COLUMN secret_hint DROP NOT NULL;
        ALTER TABLE verify.api_credential ADD CONSTRAINT api_credential_method_material CHECK (
            (auth_method = 'SECRET' AND secret_hash IS NOT NULL AND secret_hint IS NOT NULL AND public_jwk IS NULL)
            OR (auth_method = 'PRIVATE_KEY_JWT' AND secret_hash IS NULL AND secret_hint IS NULL AND public_jwk IS NOT NULL)
        );

        CREATE TABLE verify.api_assertion_jti (
            client_id   text NOT NULL,
            jti         text NOT NULL CHECK (char_length(jti) BETWEEN 8 AND 128),
            tenant_id   uuid NOT NULL,
            expires_at  timestamptz NOT NULL,
            PRIMARY KEY (client_id, jti)
        );
        CREATE INDEX api_assertion_jti_expiry ON verify.api_assertion_jti (expires_at);
        ALTER TABLE verify.api_assertion_jti ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.api_assertion_jti FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.api_assertion_jti USING ({TENANT}) WITH CHECK ({TENANT});
        CREATE POLICY verify_api_auth ON verify.api_assertion_jti USING ({AUTH}) WITH CHECK ({AUTH});
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.api_assertion_jti;
        ALTER TABLE verify.api_credential DROP CONSTRAINT api_credential_method_material;
        ALTER TABLE verify.api_credential
            DROP COLUMN public_jwk,
            DROP COLUMN auth_method;
        ALTER TABLE verify.api_application DROP COLUMN ip_allowlist;
    """)
