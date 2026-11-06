"""Software key store for a cell (CR-057): `keys.key_version`.

Revision ID: keys_0001
Revises:
Create Date: 2026-11-06

Holds keys wrapped by a master key kept outside the database, for deployments without a KMS/HSM
(`ZID_KEY_PROVIDER=db`). Cell database only; the global plane has its own table (locator_0002).
"""

from zid_platform.key_store_ddl import key_version_ddl, key_version_drop_ddl
from zid_platform.migrations import execute_script as _run

revision = "keys_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run(key_version_ddl("keys"))


def downgrade() -> None:
    _run(key_version_drop_ddl("keys"))
