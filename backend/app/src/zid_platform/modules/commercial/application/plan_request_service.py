"""Plan requests: a company asks for a different plan, the commercial team answers (ZID-COM-001 §10; CR-032).

A request changes nothing about what the company is entitled to. The move is a signed order raised and approved by the
people with the authority to do it; this only records the ask and the answer, and both go into the commercial change
log, so there is a trail from the wish to the order.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7

from zid_platform.modules.commercial.application.change_log import log_change
from zid_platform.modules.commercial.application.plan_offer_service import offer_view
from zid_platform.modules.commercial.domain import plan_requests as rules
from zid_platform.modules.commercial.domain.catalogue import DEFAULT_PLAN
from zid_platform.modules.commercial.infrastructure import catalogue_store
from zid_platform.modules.commercial.infrastructure import plan_request_store as store


def _view(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": str(r["request_id"]),
        "plan": r["plan_code"],
        "current_plan": r["current_plan"],
        "note": r["note"],
        "desired_amount": format(Decimal(r["desired_amount"]).quantize(Decimal("0.01")), "f")
        if r.get("desired_amount") is not None
        else None,
        "status": r["status"],
        "created_at": r["created_at"].isoformat(),
        "decided_at": r["decided_at"].isoformat() if r["decided_at"] else None,
        "decision_note": r["decision_note"],
        # a price for this company, once the team has made one: only an offer the company can still buy at is shown as such
        "offer": offer_view(r),
    }


async def _own_name(conn: AsyncConnection, tenant_id: str) -> str | None:
    """The asking company's display name, read under its own tenant context, so the operator list can show it."""
    row = (
        await conn.execute(
            text("SELECT display_name FROM platform.tenant WHERE tenant_id = :t"),
            {"t": uuid.UUID(tenant_id)},
        )
    ).first()
    return str(row[0]) if row and row[0] else None


async def request_plan(
    conn: AsyncConnection,
    tenant_id: str,
    principal_id: str,
    plan: str,
    note: str | None,
    desired_amount: Decimal | None = None,
) -> dict[str, Any]:
    if desired_amount is not None and not (Decimal(0) < desired_amount <= Decimal(1_000_000)):
        raise rules.PlanRequestError(
            "VALIDATION_FAILED", "the price you ask for must be more than zero"
        )
    current = await catalogue_store.active_entitlement(conn)
    current_plan = str(current["plan_code"]) if current else DEFAULT_PLAN
    published = {str(p["plan_code"]) for p in await catalogue_store.plans(conn)}
    clean = rules.check_request(
        plan=plan,
        current=current_plan,
        published=published,
        note=note,
        has_open=await store.has_open(conn),
    )
    request_id = str(uuid7())
    await store.insert(
        conn,
        request_id=request_id,
        tenant_id=tenant_id,
        plan_code=plan,
        current_plan=current_plan,
        note=clean,
        requested_by=principal_id,
        company_name=await _own_name(conn, tenant_id),
        desired_amount=desired_amount,
    )
    await log_change(
        conn,
        kind="PLAN_REQUEST_OPENED",
        ref=request_id,
        tenant_id=tenant_id,
        actor=principal_id,
        reason=None,
        detail={
            "plan": plan,
            "from": current_plan,
            "desired_amount": format(desired_amount, "f") if desired_amount is not None else None,
        },
    )
    return {
        "request_id": request_id,
        "status": rules.OPEN,
        "plan": plan,
        "current_plan": current_plan,
    }


async def my_requests(conn: AsyncConnection) -> list[dict[str, Any]]:
    return [_view(r) for r in await store.mine(conn)]


async def withdraw(
    conn: AsyncConnection, tenant_id: str, principal_id: str, request_id: str
) -> dict[str, Any]:
    row = await store.load(conn, request_id)
    if row is None:
        raise rules.PlanRequestError("NOT_FOUND", "no such request")
    rules.check_withdraw(str(row["status"]))
    await store.withdraw(conn, request_id)
    await log_change(
        conn, kind="PLAN_REQUEST_WITHDRAWN", ref=request_id, tenant_id=tenant_id, actor=principal_id
    )
    return {"request_id": request_id, "status": rules.WITHDRAWN}


async def open_requests(conn: AsyncConnection) -> list[dict[str, Any]]:
    """Operator plane: every company's open requests, oldest first. Call inside `store.operator_scope`."""
    return [
        {**_view(r), "tenant_id": str(r["tenant_id"]), "company": r.get("company_name")}
        for r in await store.open_for_operator(conn)
    ]


async def answer(
    conn: AsyncConnection, request_id: str, *, decision: str, operator_id: str, note: str | None
) -> dict[str, Any]:
    """Operator plane: ACCEPTED means the team will raise the order; DECLINED says why. Inside `operator_scope`."""
    row = await store.load(conn, request_id)
    if row is None:
        raise rules.PlanRequestError("NOT_FOUND", "no such request")
    rules.check_answer(str(row["status"]), decision)
    await store.answer(
        conn,
        request_id,
        status=decision,
        by=operator_id,
        note=(note or "").strip() or None,
        now=datetime.now(UTC),
    )
    await log_change(
        conn,
        kind=f"PLAN_REQUEST_{decision}",
        ref=request_id,
        tenant_id=str(row["tenant_id"]),
        actor=operator_id,
        reason=(note or "").strip() or None,
        detail={"plan": row["plan_code"]},
    )
    return {
        "request_id": request_id,
        "status": decision,
        "tenant_id": str(row["tenant_id"]),
        "plan": row["plan_code"],
        "note": (note or "").strip() or None,
    }


PlanRequestError = rules.PlanRequestError
__all__ = ["PlanRequestError", "answer", "my_requests", "open_requests", "request_plan", "withdraw"]
