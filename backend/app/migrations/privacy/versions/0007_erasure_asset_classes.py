"""Erasure tasks name the biometric asset classes they cover (ZID-DTV-WF-04 B05; QA-022, QA-023; CR-108).

Revision ID: privacy_0007
Revises: privacy_0006
Create Date: 2026-11-17

A first-party task covers the class of the object it disposed; a provider task covers every class of the subject's objects that provider holds. The
job and its proof then say, per class, whether it is purged (every covering task and the backups acknowledged), held, failed or pending. Each value
must be a class id (`biometric.<name>`); the list is empty for any other personal data.
"""

from zid_platform.migrations import execute_script as _run

revision = "privacy_0007"
down_revision = "privacy_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        ALTER TABLE privacy.erasure_task
            ADD COLUMN asset_classes text[] NOT NULL DEFAULT '{}',
            ADD CONSTRAINT erasure_task_asset_classes_valid
                CHECK (array_to_string(asset_classes, ',') ~ '^(biometric\\.[a-z][a-z0-9_]{1,62}(,biometric\\.[a-z][a-z0-9_]{1,62})*)?$');
    """)


def downgrade() -> None:
    _run("""
        ALTER TABLE privacy.erasure_task
            DROP CONSTRAINT erasure_task_asset_classes_valid,
            DROP COLUMN asset_classes;
    """)
