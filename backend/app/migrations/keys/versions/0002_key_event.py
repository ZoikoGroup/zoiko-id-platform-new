"""The audit trail of the software key store (CR-060): `keys.key_event`.

Revision ID: keys_0002
Revises: keys_0001
Create Date: 2026-11-06

Every create, rotate, retire, destroy and re-wrap of a key is a row, written in the same transaction as the change. The evidence worker
copies each into the ledger. Cell database only; the global plane has its own table (locator_0003).
"""

from zid_platform.key_store_ddl import key_event_ddl, key_event_drop_ddl
from zid_platform.migrations import execute_script as _run

revision = "keys_0002"
down_revision = "keys_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run(key_event_ddl("keys"))


def downgrade() -> None:
    _run(key_event_drop_ddl("keys"))
