"""Plan requests in PostgreSQL (CR-032, CR-148). Tenant rows are scoped by RLS; the operator reads across companies only inside
`operator_scope`, which the operator surface sets for the one transaction it needs.

A request can also carry a price for that one company (an "offer"): see migration commercial_0044. The offer decides only the amount the company pays when it buys the plan."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_COLS = (
    "request_id, tenant_id, plan_code, current_plan, note, requested_by, status, created_at, decided_by, decided_at, "
    "decision_note, company_name, desired_amount, offer_amount, offer_list_amount, offer_currency, offer_price_book_id, "
    "offer_expires_at, offered_by, offered_at, offer_approved_by, offer_state"
)


@asynccontextmanager
async def operator_scope(conn: AsyncConnection) -> AsyncIterator[None]:
    """Let the operator plane see and answer every company's requests for the enclosed statements only."""
    await conn.execute(text("SELECT set_config('app.commercial_scope', 'operator', true)"))
    try:
        yield
    finally:
        await conn.execute(text("SELECT set_config('app.commercial_scope', '', true)"))


def _row(r: Any) -> dict[str, Any]:
    return dict(r._mapping)


async def insert(
    conn: AsyncConnection,
    *,
    request_id: str,
    tenant_id: str,
    plan_code: str,
    current_plan: str,
    note: str | None,
    requested_by: str,
    company_name: str | None = None,
    desired_amount: Decimal | None = None,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO commercial.plan_request (request_id, tenant_id, plan_code, current_plan, note, requested_by, "
            "company_name, desired_amount) VALUES (:id, :t, :plan, :cur, :note, :by, :co, :want)"
        ),
        {
            "id": uuid.UUID(request_id),
            "t": uuid.UUID(tenant_id),
            "plan": plan_code,
            "cur": current_plan,
            "note": note or None,
            "by": uuid.UUID(requested_by),
            "co": company_name,
            "want": desired_amount,
        },
    )


async def has_open(conn: AsyncConnection) -> bool:
    return bool(
        (
            await conn.execute(
                text("SELECT 1 FROM commercial.plan_request WHERE status = 'OPEN' LIMIT 1")
            )
        ).first()
    )


async def mine(conn: AsyncConnection, limit: int = 20) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(f"SELECT {_COLS} FROM commercial.plan_request ORDER BY created_at DESC LIMIT :n"),
            {"n": limit},
        )
    ).all()
    return [_row(r) for r in rows]


async def load(conn: AsyncConnection, request_id: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(f"SELECT {_COLS} FROM commercial.plan_request WHERE request_id = :i FOR UPDATE"),
            {"i": uuid.UUID(request_id)},
        )
    ).first()
    return _row(row) if row else None


async def withdraw(conn: AsyncConnection, request_id: str) -> None:
    await conn.execute(
        text(
            "UPDATE commercial.plan_request SET status = 'WITHDRAWN' WHERE request_id = :i AND status = 'OPEN'"
        ),
        {"i": uuid.UUID(request_id)},
    )


async def open_for_operator(conn: AsyncConnection, limit: int = 100) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                f"SELECT {_COLS} FROM commercial.plan_request WHERE status = 'OPEN' ORDER BY created_at LIMIT :n"
            ),
            {"n": limit},
        )
    ).all()
    return [_row(r) for r in rows]


async def answer(
    conn: AsyncConnection, request_id: str, *, status: str, by: str, note: str | None, now: datetime
) -> None:
    await conn.execute(
        text(
            "UPDATE commercial.plan_request SET status = :s, decided_by = :by, decided_at = :at, decision_note = :n "
            "WHERE request_id = :i AND status = 'OPEN'"
        ),
        {
            "s": status,
            "by": uuid.UUID(by),
            "at": now,
            "n": note or None,
            "i": uuid.UUID(request_id),
        },
    )


# ------------------------------------------------------------------------------- offers (CR-148)


async def set_offer(
    conn: AsyncConnection,
    request_id: str,
    *,
    amount: Decimal,
    list_amount: Decimal,
    currency: str,
    price_book_id: str,
    expires_at: datetime,
    by: str,
    now: datetime,
    state: str,
    approved_by: str | None,
    note: str | None,
) -> None:
    """Answer an OPEN request with an offer: the request becomes ACCEPTED (the team said yes, at this price) and carries the price."""
    await conn.execute(
        text(
            "UPDATE commercial.plan_request SET status = 'ACCEPTED', decided_by = :by, decided_at = :at, decision_note = :n, "
            "offer_amount = :a, offer_list_amount = :l, offer_currency = :c, offer_price_book_id = :b, offer_expires_at = :x, "
            "offered_by = :by, offered_at = :at, offer_state = :s, offer_approved_by = :ap "
            "WHERE request_id = :i AND status = 'OPEN'"
        ),
        {
            "i": uuid.UUID(request_id),
            "by": uuid.UUID(by),
            "at": now,
            "n": note or None,
            "a": amount,
            "l": list_amount,
            "c": currency,
            "b": uuid.UUID(price_book_id),
            "x": expires_at,
            "s": state,
            "ap": uuid.UUID(approved_by) if approved_by else None,
        },
    )


async def approve_offer(conn: AsyncConnection, request_id: str, *, approver: str) -> None:
    await conn.execute(
        text(
            "UPDATE commercial.plan_request SET offer_state = 'OPEN', offer_approved_by = :ap "
            "WHERE request_id = :i AND offer_state = 'PENDING_APPROVAL'"
        ),
        {"i": uuid.UUID(request_id), "ap": uuid.UUID(approver)},
    )


async def withdraw_offer(conn: AsyncConnection, request_id: str) -> None:
    await conn.execute(
        text(
            "UPDATE commercial.plan_request SET offer_state = 'WITHDRAWN' "
            "WHERE request_id = :i AND offer_state IN ('PENDING_APPROVAL', 'OPEN')"
        ),
        {"i": uuid.UUID(request_id)},
    )


async def open_offer(conn: AsyncConnection, plan_code: str, now: datetime) -> dict[str, Any] | None:
    """The one offer this company may buy `plan_code` at right now: OPEN and not past its last day (the newest if there are several). Row-level security scopes it to the company."""
    row = (
        await conn.execute(
            text(
                f"SELECT {_COLS} FROM commercial.plan_request WHERE plan_code = :p AND offer_state = 'OPEN' "
                "AND offer_expires_at > :n ORDER BY offered_at DESC LIMIT 1"
            ),
            {"p": plan_code, "n": now},
        )
    ).first()
    return _row(row) if row else None


async def mark_offer_used(conn: AsyncConnection, plan_code: str) -> None:
    """The plan was bought and switched on at an offered price: that offer is spent. Row-level security limits it to the company."""
    await conn.execute(
        text(
            "UPDATE commercial.plan_request SET offer_state = 'USED' WHERE plan_code = :p AND offer_state = 'OPEN'"
        ),
        {"p": plan_code},
    )


async def offers_for_operator(conn: AsyncConnection, limit: int = 100) -> list[dict[str, Any]]:
    """Operator plane: offers still waiting for approval or for the company to buy, newest first. Call inside `operator_scope`."""
    rows = (
        await conn.execute(
            text(
                f"SELECT {_COLS} FROM commercial.plan_request WHERE offer_state IN ('PENDING_APPROVAL', 'OPEN') "
                "ORDER BY offered_at DESC LIMIT :n"
            ),
            {"n": limit},
        )
    ).all()
    return [_row(r) for r in rows]
