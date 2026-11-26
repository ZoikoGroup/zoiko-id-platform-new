"""The partner request log writer (ZID-CP-WF10 v2.0, WF-10.10; CR-173).

One row per call a partner's server made with a bearer token: the route template, the status, the error code, a trace id and how long it took. Nothing from the body, headers or
query string is kept. A failure to write the row never changes the response the partner gets: the log is a record, not a gate.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine
from zid_context import RequestContext
from zid_db import tenant_transaction

from zid_platform.modules.verify_api.domain import diagnostics as dg
from zid_platform.modules.verify_api.infrastructure import store

log = logging.getLogger("zid.verify_api.request_log")


async def record(engine: AsyncEngine, who: Any, *, method: str, template: str | None, path: str, status: int, error_code: str | None, trace_id: str, session_id: str | None, duration_ms: int) -> None:
    try:
        ctx = RequestContext("req_log", trace_id or "cor_unknown", "zoiko-id", "unknown").with_identity(who.tenant_id, None)
        async with tenant_transaction(engine, ctx) as conn:
            await store.insert_request_log(
                conn,
                {
                    "tenant_id": who.tenant_id, "application_id": who.application_id, "environment": who.environment, "method": method[:10], "route": dg.route_template(template, "(unknown)"),
                    "status": status, "error_code": (error_code or None), "trace_id": (trace_id or "unknown")[:80], "session_id": session_id, "duration_ms": max(0, duration_ms),
                },
            )
    except Exception:  # noqa: BLE001 - the log must never break a partner call
        log.warning("verify api request log write failed", exc_info=True)
