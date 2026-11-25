"""Public interface of the verify_analytics module (ZID-CPS-WF-02 Command Center). Other modules and entrypoints import ONLY this.

- `ingest(conn, event)` / `wanted(type)` / `DOMAINS`: the ingest consumer's fact writer and what it subscribes to
- `purge_expired(conn, before=)`: the worker's retention sweep
"""

from __future__ import annotations

from zid_platform.modules.verify_analytics.application.ingest import (
    DOMAINS,
    ingest,
    purge_expired,
    wanted,
)

__all__ = ["DOMAINS", "ingest", "purge_expired", "wanted"]
