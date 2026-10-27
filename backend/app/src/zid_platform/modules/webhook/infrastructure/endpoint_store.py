"""Reads and status changes of webhook endpoints and deliveries (tenant transactions, RLS on)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.webhook.domain.webhooks import WebhookError

_ENDPOINT = (
    "SELECT endpoint_id, url, event_types, status, signing_key_id, max_inflight, created_by, "
    "created_at, updated_at FROM events.webhook_endpoint"
)
_DELIVERY = (
    "SELECT delivery_id, endpoint_id, event_id, event_type, payload_digest, status, attempts, "
    "next_attempt_at, last_status_code, last_error, replay_of, created_at, delivered_at "
    "FROM events.webhook_delivery"
)


def _iso(v: Any) -> str | None:
    return v.isoformat() if v is not None else None


def _endpoint(r: Any) -> dict[str, Any]:
    return {
        "endpoint_id": str(r.endpoint_id),
        "url": r.url,
        "event_types": list(r.event_types),
        "status": r.status,
        "signing_key_id": r.signing_key_id,
        "max_inflight": r.max_inflight,
        "created_by": str(r.created_by),
        "created_at": _iso(r.created_at),
        "updated_at": _iso(r.updated_at),
    }


def _delivery(r: Any) -> dict[str, Any]:
    return {
        "delivery_id": str(r.delivery_id),
        "endpoint_id": str(r.endpoint_id),
        "event_id": str(r.event_id),
        "event_type": r.event_type,
        "payload_digest": r.payload_digest,
        "status": r.status,
        "attempts": r.attempts,
        "next_attempt_at": _iso(r.next_attempt_at),
        "last_status_code": r.last_status_code,
        "last_error": r.last_error,
        "replay_of": str(r.replay_of) if r.replay_of else None,
        "created_at": _iso(r.created_at),
        "delivered_at": _iso(r.delivered_at),
    }


async def list_endpoints(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (await conn.execute(text(f"{_ENDPOINT} ORDER BY created_at DESC LIMIT 200"))).all()
    return [_endpoint(r) for r in rows]


async def load_endpoint(conn: AsyncConnection, endpoint_id: str) -> dict[str, Any]:
    row = (
        await conn.execute(
            text(f"{_ENDPOINT} WHERE endpoint_id = :e"), {"e": uuid.UUID(endpoint_id)}
        )
    ).first()
    if row is None:
        raise WebhookError("WEBHOOK_ENDPOINT_NOT_FOUND")
    return _endpoint(row)


async def set_status(
    conn: AsyncConnection, endpoint_id: str, *, to: str, allowed_from: tuple[str, ...]
) -> dict[str, Any]:
    result = await conn.execute(
        text(
            "UPDATE events.webhook_endpoint SET status = :to, updated_at = now() "
            "WHERE endpoint_id = :e AND status = ANY(:from)"
        ),
        {"to": to, "e": uuid.UUID(endpoint_id), "from": list(allowed_from)},
    )
    if result.rowcount != 1:
        current = await load_endpoint(conn, endpoint_id)  # 404 when it does not exist
        raise WebhookError(
            "WEBHOOK_ENDPOINT_STATE", f"cannot move a {current['status']} endpoint to {to}"
        )
    return await load_endpoint(conn, endpoint_id)


async def list_deliveries(
    conn: AsyncConnection, *, endpoint_id: str | None = None, status: str | None = None
) -> list[dict[str, Any]]:
    where: list[str] = []
    params: dict[str, Any] = {}
    if endpoint_id:
        where.append("endpoint_id = :e")
        params["e"] = uuid.UUID(endpoint_id)
    if status:
        where.append("status = :s")
        params["s"] = status
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    rows = (
        await conn.execute(text(f"{_DELIVERY}{clause} ORDER BY created_at DESC LIMIT 200"), params)
    ).all()
    return [_delivery(r) for r in rows]


async def load_delivery(conn: AsyncConnection, delivery_id: str) -> dict[str, Any]:
    row = (
        await conn.execute(
            text(f"{_DELIVERY} WHERE delivery_id = :d"), {"d": uuid.UUID(delivery_id)}
        )
    ).first()
    if row is None:
        raise WebhookError("WEBHOOK_DELIVERY_NOT_FOUND")
    return _delivery(row)
