"""OID4VP HAIP: encrypted direct_post.jwt responses.

Revision ID: credential_0002
Revises: credential_0001
Create Date: 2026-10-13

ZID-VC-001 §11; OpenID4VC HAIP 1.0.
- response_mode: direct_post (form) or direct_post.jwt (the wallet encrypts its response to us).
- response_key: the ephemeral ECDH-ES private key of this one presentation. It exists only while
  the transaction is open (<= 10 minutes) and is wiped when the presentation completes, expires
  or is rejected, so a later database read cannot decrypt a captured response.
"""

from zid_platform.migrations import execute_script as _run

revision = "credential_0002"
down_revision = "credential_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE cred.presentation_record
            ADD COLUMN response_mode text NOT NULL DEFAULT 'direct_post'
                CHECK (response_mode IN ('direct_post', 'direct_post.jwt')),
            ADD COLUMN response_key jsonb,
            ADD CONSTRAINT presentation_response_key
                CHECK (response_mode = 'direct_post' AND response_key IS NULL
                       OR response_mode = 'direct_post.jwt'
                          AND (status = 'REQUESTED') = (response_key IS NOT NULL));
    """)


def downgrade() -> None:
    _run("""
        ALTER TABLE cred.presentation_record
            DROP CONSTRAINT presentation_response_key,
            DROP COLUMN response_key,
            DROP COLUMN response_mode;
    """)
