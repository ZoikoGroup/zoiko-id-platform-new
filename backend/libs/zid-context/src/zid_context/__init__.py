"""Request context for every Zoiko iD request (ZID-SVC-001 §8, ZID-API-001 §1.1).

Tenant and principal are *server-derived*: they are set only by the authentication layer
(W1), never from request headers or bodies. `X-Zoiko-Tenant-Id` is advisory and is not read here.
"""

from __future__ import annotations

import os
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, replace

__all__ = ["RequestContext", "current_context", "new_id", "reset_context", "set_context", "uuid7"]


def uuid7() -> uuid.UUID:
    """RFC 9562 UUIDv7: 48-bit Unix ms timestamp + random bits (time-ordered, index-friendly)."""
    ts_ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ts_ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76  # version 7
    value |= ((rand >> 62) & 0xFFF) << 64  # rand_a (12 bits)
    value |= 0b10 << 62  # RFC 4122 variant
    value |= rand & ((1 << 62) - 1)  # rand_b (62 bits)
    return uuid.UUID(int=value)


def new_id(prefix: str) -> str:
    """Opaque prefixed identifier, e.g. `req_0199...` (ZID-API-001 §3.1)."""
    return f"{prefix}_{uuid7().hex}"


@dataclass(frozen=True, slots=True)
class RequestContext:
    request_id: str
    correlation_id: str
    service: str
    cell_id: str
    tenant_id: str | None = None  # server-derived only (set by authentication, W1)
    principal_id: str | None = None  # server-derived only

    def with_identity(self, tenant_id: str, principal_id: str | None) -> RequestContext:
        return replace(self, tenant_id=tenant_id, principal_id=principal_id)


_current: ContextVar[RequestContext | None] = ContextVar("zid_request_context", default=None)


def current_context() -> RequestContext | None:
    return _current.get()


def set_context(ctx: RequestContext) -> object:
    return _current.set(ctx)


def reset_context(token: object) -> None:
    _current.reset(token)  # type: ignore[arg-type]
