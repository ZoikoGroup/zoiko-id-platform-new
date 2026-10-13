"""SAML encrypted assertions and governed IdP-initiated SSO.

Revision ID: federation_0004
Revises: federation_0003
Create Date: 2026-10-13

ZID-AUTH-001 §10.
- sp_decryption_key_ref: brokered reference to the SP's RSA decryption key (never the key itself);
  require_encrypted_assertions refuses plaintext assertions once encryption is agreed.
- idp_initiated_targets: where an unsolicited login may land. IdP-initiated SSO stays off until an
  R3 change with a second administrator's approval sets allow_idp_initiated, and then only with at
  least one exact https target (RelayState never becomes an open redirect).
"""

from zid_platform.migrations import execute_script as _run

revision = "federation_0004"
down_revision = "federation_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE authn.federation_connection
            ADD COLUMN sp_decryption_key_ref text CHECK (char_length(sp_decryption_key_ref) <= 128),
            ADD COLUMN require_encrypted_assertions boolean NOT NULL DEFAULT false,
            ADD COLUMN idp_initiated_targets text[] NOT NULL DEFAULT '{}',
            ADD CONSTRAINT federation_encryption_needs_key
                CHECK (NOT require_encrypted_assertions OR sp_decryption_key_ref IS NOT NULL),
            ADD CONSTRAINT federation_idp_initiated_saml_only
                CHECK (NOT allow_idp_initiated OR (protocol = 'SAML'
                       AND cardinality(idp_initiated_targets) BETWEEN 1 AND 10));
    """)


def downgrade() -> None:
    _run("""
        ALTER TABLE authn.federation_connection
            DROP CONSTRAINT federation_idp_initiated_saml_only,
            DROP CONSTRAINT federation_encryption_needs_key,
            DROP COLUMN idp_initiated_targets,
            DROP COLUMN require_encrypted_assertions,
            DROP COLUMN sp_decryption_key_ref;
    """)
