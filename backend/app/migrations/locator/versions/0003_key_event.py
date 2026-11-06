"""The audit trail of the global plane's software keys (CR-060): `global_directory.key_event`.

Revision ID: locator_0003
Revises: locator_0002
Create Date: 2026-11-06

The global plane has no evidence ledger (it holds no personal data and loads no cell modules), so these rows are its audit trail: append-only,
written in the same transaction as the key change.
"""

from zid_platform.key_store_ddl import key_event_ddl, key_event_drop_ddl
from zid_platform.migrations import execute_script as _run

revision = "locator_0003"
down_revision = "locator_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run(key_event_ddl("global_directory"))


def downgrade() -> None:
    _run(key_event_drop_ddl("global_directory"))
