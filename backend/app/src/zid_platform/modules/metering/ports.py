"""Public Protocol interface of the metering module. Other modules import ONLY this.

- `usage_summary(conn, days=)`: the tenant's daily API calls per surface and status class (read inside the tenant's transaction)
"""

from __future__ import annotations

from zid_platform.modules.metering.infrastructure.usage_store import summary as usage_summary

__all__ = ["usage_summary"]
