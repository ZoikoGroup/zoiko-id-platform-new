"""Public interface of the configuration module. Other modules import ONLY this.

- `session_limits(conn)`: the tenant's chosen (absolute lifetime, idle timeout); each None = platform default.
- `muted_alert_kinds(conn)`: non-mandatory alert kinds the tenant has muted.
- `session_expiry(conn, now, platform_ttl)`: when a session created now ends, after the tenant's choice.
- `public_branding(conn)`: presentation-only branding, safe to show before sign-in.
"""

from __future__ import annotations

from zid_platform.modules.configuration.application.settings import (
    muted_alert_kinds,
    public_branding,
    session_expiry,
    session_limits,
)

__all__ = ["muted_alert_kinds", "public_branding", "session_expiry", "session_limits"]
