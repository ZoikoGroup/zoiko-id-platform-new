"""Uploaded evidence is encrypted per upload under the tenant's key (ZID-PRI-004 PR-05, PRI-SEC-01; CR-072).

Revision ID: evidence_0009
Revises: evidence_0008
Create Date: 2026-11-12

Each new upload's bytes are sealed with AES-256-GCM under a random data key, and that key is wrapped by the tenant's key-encryption key through the
evidence runtime (so a customer-held key that is switched off makes the bytes unreadable, and erasing the wrapped key is a cryptographic erasure).
`content_encrypted` is false only for rows written before this change, which stay readable. A READY encrypted row must carry its wrapped key.
"""

from zid_platform.migrations import execute_script as _run

revision = "evidence_0009"
down_revision = "evidence_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE evidence.upload
            ADD COLUMN content_encrypted boolean NOT NULL DEFAULT false,
            ADD COLUMN wrapped_dek       bytea,
            ADD COLUMN dek_kek_id        text,
            ADD COLUMN dek_kek_version   integer,
            ADD CONSTRAINT upload_encrypted_has_key
                CHECK (NOT content_encrypted OR content IS NULL OR (wrapped_dek IS NOT NULL AND dek_kek_id IS NOT NULL));
    """)


def downgrade() -> None:
    _run("""
        ALTER TABLE evidence.upload
            DROP CONSTRAINT upload_encrypted_has_key,
            DROP COLUMN content_encrypted, DROP COLUMN wrapped_dek, DROP COLUMN dek_kek_id, DROP COLUMN dek_kek_version;
    """)
