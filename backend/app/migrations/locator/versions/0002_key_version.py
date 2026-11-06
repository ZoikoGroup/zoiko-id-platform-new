"""Software key store for the global plane (CR-060): `global_directory.key_version`.

Revision ID: locator_0002
Revises: locator_0001
Create Date: 2026-11-06

Holds the global plane's discovery signing key, wrapped by a master key kept outside the database
(`ZID_KEY_PROVIDER=db`). Global-plane database only.
"""

from zid_platform.key_store_ddl import key_version_ddl, key_version_drop_ddl
from zid_platform.migrations import execute_script as _run

revision = "locator_0002"
down_revision = "locator_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run(key_version_ddl("global_directory"))


def downgrade() -> None:
    _run(key_version_drop_ddl("global_directory"))
