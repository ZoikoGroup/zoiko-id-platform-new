"""Public Protocol interface of the locator module (global plane only). Other modules import ONLY this.

- `publish(conn, jws, cell_keys)`: accept a cell's signed directory record (PII gate, epoch fencing).
- `resolve(conn, tenant_id=... | alias=...)`: the tenant's directory record, or None.
"""

from __future__ import annotations

from zid_platform.modules.locator.application.service import CellKeys, publish, resolve
from zid_platform.modules.locator.domain.record import (
    DISCOVERY_TYP,
    LIFECYCLE_STATUS,
    RECORD_TYP,
    DirectoryRecord,
    LocatorError,
)

__all__ = [
    "DISCOVERY_TYP",
    "LIFECYCLE_STATUS",
    "RECORD_TYP",
    "CellKeys",
    "DirectoryRecord",
    "LocatorError",
    "publish",
    "resolve",
]
