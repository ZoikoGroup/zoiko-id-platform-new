"""Client-certificate (mTLS) credentials for the token endpoint (ZID-EXT-IDV-001 WF-05, SEC-02; CR-165; docs/specs/verify-api-mtls-domains-logos-decisions.md).

Revision ID: verify_api_0011
Revises: verify_api_0010
Create Date: 2026-11-25

A third kind of credential: MTLS holds only the SHA-256 fingerprint of the partner's own certificate (a pinned leaf, never a certificate authority). The partner's server presents that certificate at the token endpoint;
the cell believes it only when it arrives from a proxy the cell has been told to trust, with that proxy's shared secret. No secret and no private key is held for such a credential.
"""

from zid_platform.migrations import execute_script as _run

revision = "verify_api_0011"
down_revision = "verify_api_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE verify.api_credential DROP CONSTRAINT api_credential_method_material;
        ALTER TABLE verify.api_credential DROP CONSTRAINT api_credential_auth_method_check;
        ALTER TABLE verify.api_credential ADD COLUMN cert_sha256 bytea CHECK (cert_sha256 IS NULL OR octet_length(cert_sha256) = 32);
        ALTER TABLE verify.api_credential ADD CONSTRAINT api_credential_auth_method_check CHECK (auth_method IN ('SECRET', 'PRIVATE_KEY_JWT', 'MTLS'));
        ALTER TABLE verify.api_credential ADD CONSTRAINT api_credential_method_material CHECK (
            (auth_method = 'SECRET' AND secret_hash IS NOT NULL AND secret_hint IS NOT NULL AND public_jwk IS NULL AND cert_sha256 IS NULL)
            OR (auth_method = 'PRIVATE_KEY_JWT' AND secret_hash IS NULL AND secret_hint IS NULL AND public_jwk IS NOT NULL AND cert_sha256 IS NULL)
            OR (auth_method = 'MTLS' AND secret_hash IS NULL AND secret_hint IS NULL AND public_jwk IS NULL AND cert_sha256 IS NOT NULL)
        );
    """)


def downgrade() -> None:
    _run("""
        DELETE FROM verify.api_credential WHERE auth_method = 'MTLS';
        ALTER TABLE verify.api_credential DROP CONSTRAINT api_credential_method_material;
        ALTER TABLE verify.api_credential DROP CONSTRAINT api_credential_auth_method_check;
        ALTER TABLE verify.api_credential DROP COLUMN cert_sha256;
        ALTER TABLE verify.api_credential ADD CONSTRAINT api_credential_auth_method_check CHECK (auth_method IN ('SECRET', 'PRIVATE_KEY_JWT'));
        ALTER TABLE verify.api_credential ADD CONSTRAINT api_credential_method_material CHECK (
            (auth_method = 'SECRET' AND secret_hash IS NOT NULL AND secret_hint IS NOT NULL AND public_jwk IS NULL)
            OR (auth_method = 'PRIVATE_KEY_JWT' AND secret_hash IS NULL AND secret_hint IS NULL AND public_jwk IS NOT NULL)
        );
    """)
