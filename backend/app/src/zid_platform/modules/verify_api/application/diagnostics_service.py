"""Endpoint health, the partner request log and the trace of one verification (ZID-CP-WF10 v2.0, WF-10.09, WF-10.10; CR-173). Runs in the caller's tenant transaction."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.ports import route_decisions_for_session
from zid_platform.modules.verify_api.domain import diagnostics as dg
from zid_platform.modules.verify_api.domain.model import PartnerError
from zid_platform.modules.verify_api.infrastructure import session_store, store
from zid_platform.modules.webhook.ports import deliveries_for_session, recent_deliveries_by_endpoint


async def endpoints(conn: AsyncConnection) -> dict[str, Any]:
    rows = await recent_deliveries_by_endpoint(conn, dg.SAMPLE)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        grouped[str(r["endpoint_id"])].append(r)
    confirmed = await store.confirmed_event_count(conn)
    items = []
    for eid, ds in grouped.items():
        h = dg.endpoint_health(ds, confirmed)
        items.append({"endpoint_id": eid, "host": ds[0]["host"], "endpoint_status": ds[0]["endpoint_status"], **h})
    return {"items": items, "note": "Read from the newest deliveries of each endpoint. An endpoint with no deliveries yet is not listed."}


async def requests(conn: AsyncConnection, application_id: str, *, status: int | None, route: str | None, before: datetime | None, limit: int) -> dict[str, Any]:
    problems = dg.request_filters_problems(status, route, limit)
    if problems:
        raise PartnerError("VALIDATION_FAILED", "; ".join(problems))
    if await store.get_application(conn, application_id) is None:
        raise PartnerError("NOT_FOUND", "No such application.")
    rows = await store.request_log(conn, application_id, status=status, route=route, before=before, limit=limit)
    return {
        "items": [{**r, "occurred_at": r["occurred_at"].isoformat()} for r in rows],
        "next_before": rows[-1]["occurred_at"].isoformat() if len(rows) == limit else None,
        "note": "Route templates, status codes, error codes and trace ids only. No body, header, query string or person data is kept.",
    }


async def trace(conn: AsyncConnection, session_id: str) -> dict[str, Any]:
    session = await session_store.get_session_by_id(conn, session_id)
    if session is None:
        raise PartnerError("NOT_FOUND", "No such session.")
    return dg.build_trace(
        session_id, {"status": session["status"]}, await store.requests_for_session(conn, session_id), await route_decisions_for_session(conn, session_id), await store.decisions_for_session(conn, session_id), await deliveries_for_session(conn, session_id)
    )
