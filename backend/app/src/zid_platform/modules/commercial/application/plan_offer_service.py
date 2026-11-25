"""A price for one company (CR-148): the team answers a plan request with the monthly amount that company will pay.

An offer is the answer "yes, at this price". It belongs to one company, one plan and one request, it is never higher than the list price in the approved price book, and it ends on a
date. It moves no plan and charges nothing: the company still buys the plan itself, the amount is the offered one, and the plan switches only after Stripe confirms that exact payment
(CR-062, CR-147, `self_serve_service`).

A price below the list price is a discount, so it is held to the same discount authority as any other (`commercial.discount_policy`):
- direct mode (one operator runs the platform): that operator is the authority, as for every other approval in direct mode;
- governed mode: the offer waits (PENDING_APPROVAL) for a different operator who holds every role the reduction needs. A reduction whose band needs more than one role is refused here, never
  approved with less authority than the band asks for.
In a deployed environment a reduction also needs the finance policy to be signed (the bands have no authority otherwise), as for quotes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.application import finance_service as finance
from zid_platform.modules.commercial.application import orders_service as orders
from zid_platform.modules.commercial.application.change_log import log_change
from zid_platform.modules.commercial.application.self_serve_service import (
    BASE_SKU,
    SELF_SERVE_PLANS,
)
from zid_platform.modules.commercial.domain import plan_requests as rules
from zid_platform.modules.commercial.infrastructure import catalogue_store
from zid_platform.modules.commercial.infrastructure import plan_request_store as store

MAX_VALID_DAYS = 60
CENT = Decimal("0.01")


class OfferError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail or code.replace("_", " ").capitalize() + "."


def _money(v: Decimal) -> str:
    return format(v.quantize(CENT), "f")


def offer_view(r: dict[str, Any]) -> dict[str, Any] | None:
    """An offer as the company and the operator read it, or None when the request carries none."""
    if r.get("offer_state") is None:
        return None
    list_amount = Decimal(r["offer_list_amount"])
    amount = Decimal(r["offer_amount"])
    return {
        "state": r["offer_state"],
        "amount": _money(amount),
        "list_amount": _money(list_amount),
        "currency": r["offer_currency"],
        "reduction_percent": format(
            ((list_amount - amount) / list_amount * 100).quantize(CENT), "f"
        ),
        "expires_at": r["offer_expires_at"].isoformat(),
        "offered_at": r["offered_at"].isoformat(),
    }


async def _list_price(conn: AsyncConnection, plan: str, now: datetime) -> tuple[Decimal, str, str]:
    book = await catalogue_store.approved_price_book(conn, now)
    if book is None:
        raise OfferError(
            "NO_APPROVED_PRICE_BOOK",
            "There is no approved price book, so there is no list price to offer against.",
        )
    line = next(
        (
            ln
            for ln in await catalogue_store.lines(conn, str(book["price_book_id"]))
            if ln["sku_id"] == BASE_SKU and ln["plan_code"] == plan and ln["released"]
        ),
        None,
    )
    if line is None or Decimal(line["unit_rate"]) <= 0:
        raise OfferError("PRICE_NOT_PUBLISHED", f"The {plan.title()} plan has no list price yet.")
    return Decimal(line["unit_rate"]), str(book["currency"]), str(book["price_book_id"])


async def make_offer(
    conn: AsyncConnection,
    request_id: str,
    *,
    amount: Decimal,
    valid_days: int,
    note: str | None,
    operator_id: str,
    direct: bool,
    direct_approver: str,
    policy_lock: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Answer an open request with a price for that company. Call inside `plan_request_store.operator_scope`."""
    now = now or datetime.now(UTC)
    row = await store.load(conn, request_id)
    if row is None:
        raise rules.PlanRequestError("NOT_FOUND", "no such request")
    rules.check_answer(str(row["status"]), rules.ACCEPTED)
    plan = str(row["plan_code"])
    if plan not in SELF_SERVE_PLANS:
        raise OfferError(
            "PLAN_NOT_SELF_SERVE",
            f"{plan.title()} is agreed in a contract, not bought online, so it has no offer.",
        )
    if not 1 <= valid_days <= MAX_VALID_DAYS:
        raise OfferError("OFFER_VALIDITY", f"An offer lasts between 1 and {MAX_VALID_DAYS} days.")
    price = amount.quantize(CENT, rounding=ROUND_HALF_UP)
    if price <= 0:
        raise OfferError("OFFER_NOT_POSITIVE", "The price must be more than zero.")
    list_amount, currency, book_id = await _list_price(conn, plan, now)
    if price > list_amount:
        raise OfferError(
            "OFFER_ABOVE_LIST",
            f"An offer cannot be above the list price of {_money(list_amount)} {currency} a month. The company can buy at the list price without an offer.",
        )
    discount = (list_amount - price) / list_amount
    roles = await orders.discount_roles(conn, discount)
    if (
        discount > 0
        and not direct
        and policy_lock
        and not (await finance.policy_state(conn))["effective"]
    ):
        raise OfferError(
            "POLICY_NOT_SIGNED",
            "The finance policy is not signed by the Founder and the CFO, so a price below the list price has no authority yet.",
        )
    if discount == 0:
        state, approved_by = (
            "OPEN",
            operator_id,
        )  # the list price needs no authority: it is what anyone can buy at
    elif direct:
        state, approved_by = "OPEN", direct_approver
    else:
        if len(roles) > 1:
            raise OfferError(
                "OFFER_NEEDS_SEVERAL_ROLES",
                f"A reduction of {(discount * 100).quantize(CENT)}% needs {' and '.join(sorted(roles))}, each from a different person. Offer a smaller reduction, or raise it as a quote.",
            )
        state, approved_by = "PENDING_APPROVAL", None
    expires = now + timedelta(days=valid_days)
    await store.set_offer(
        conn,
        request_id,
        amount=price,
        list_amount=list_amount,
        currency=currency,
        price_book_id=book_id,
        expires_at=expires,
        by=operator_id,
        now=now,
        state=state,
        approved_by=approved_by,
        note=(note or "").strip() or None,
    )
    await log_change(
        conn,
        kind="PLAN_OFFER_MADE",
        ref=request_id,
        tenant_id=str(row["tenant_id"]),
        actor=operator_id,
        reason=(note or "").strip() or None,
        detail={
            "plan": plan,
            "amount": _money(price),
            "list_amount": _money(list_amount),
            "currency": currency,
            "reduction_percent": format((discount * 100).quantize(CENT), "f"),
            "state": state,
            "roles": sorted(roles),
            "valid_until": expires.isoformat(),
        },
    )
    return {
        "request_id": request_id,
        "tenant_id": str(row["tenant_id"]),
        "plan": plan,
        "company": row.get("company_name"),
        "offer": {
            "state": state,
            "amount": _money(price),
            "list_amount": _money(list_amount),
            "currency": currency,
            "reduction_percent": format((discount * 100).quantize(CENT), "f"),
            "expires_at": expires.isoformat(),
        },
        "awaiting_approval": state == "PENDING_APPROVAL",
        "note": (note or "").strip() or None,
    }


async def approve_offer(
    conn: AsyncConnection,
    request_id: str,
    *,
    approver_id: str,
    policy_lock: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """A different operator, holding every role the reduction needs, lets the company buy at the offered price."""
    now = now or datetime.now(UTC)
    row = await store.load(conn, request_id)
    if row is None:
        raise rules.PlanRequestError("NOT_FOUND", "no such request")
    if row["offer_state"] != "PENDING_APPROVAL":
        raise OfferError("OFFER_NOT_AWAITING_APPROVAL", "That offer is not waiting for approval.")
    if str(row["offered_by"]) == approver_id:
        raise OfferError("OFFER_SELF_APPROVAL", "The person who made the offer cannot approve it.")
    if row["offer_expires_at"] <= now:
        raise OfferError("OFFER_EXPIRED", "That offer has already ended.")
    if policy_lock and not (await finance.policy_state(conn))["effective"]:
        raise OfferError(
            "POLICY_NOT_SIGNED",
            "The finance policy is not signed by the Founder and the CFO, so a price below the list price has no authority yet.",
        )
    list_amount = Decimal(row["offer_list_amount"])
    discount = (list_amount - Decimal(row["offer_amount"])) / list_amount
    for role in sorted(await orders.discount_roles(conn, discount)):
        if not await orders.holds_role(conn, approver_id, role):
            raise OfferError(
                "APPROVER_ROLE_MISSING", f"Approving this price needs the {role} role."
            )
    await store.approve_offer(conn, request_id, approver=approver_id)
    await log_change(
        conn,
        kind="PLAN_OFFER_APPROVED",
        ref=request_id,
        tenant_id=str(row["tenant_id"]),
        actor=approver_id,
        detail={"plan": row["plan_code"], "amount": _money(Decimal(row["offer_amount"]))},
    )
    fresh = await store.load(conn, request_id)
    assert fresh is not None
    return {
        "request_id": request_id,
        "tenant_id": str(row["tenant_id"]),
        "plan": str(row["plan_code"]),
        "company": row.get("company_name"),
        "offer": offer_view(fresh),
    }


async def withdraw_offer(
    conn: AsyncConnection, request_id: str, *, operator_id: str, reason: str | None
) -> dict[str, Any]:
    """The team takes an offer back. A plan already bought at it is untouched; the company simply can no longer buy at that price."""
    row = await store.load(conn, request_id)
    if row is None:
        raise rules.PlanRequestError("NOT_FOUND", "no such request")
    if row["offer_state"] not in ("PENDING_APPROVAL", "OPEN"):
        raise OfferError("OFFER_NOT_ACTIVE", "That offer is not waiting to be used.")
    await store.withdraw_offer(conn, request_id)
    await log_change(
        conn,
        kind="PLAN_OFFER_WITHDRAWN",
        ref=request_id,
        tenant_id=str(row["tenant_id"]),
        actor=operator_id,
        reason=(reason or "").strip() or None,
        detail={"plan": row["plan_code"]},
    )
    return {
        "request_id": request_id,
        "tenant_id": str(row["tenant_id"]),
        "plan": str(row["plan_code"]),
        "state": "WITHDRAWN",
    }


async def waiting(conn: AsyncConnection) -> list[dict[str, Any]]:
    """Operator plane: offers still waiting for approval or for the company to buy. Call inside `plan_request_store.operator_scope`."""
    out = []
    for r in await store.offers_for_operator(conn):
        out.append(
            {
                "request_id": str(r["request_id"]),
                "tenant_id": str(r["tenant_id"]),
                "company": r.get("company_name"),
                "plan": r["plan_code"],
                "offer": offer_view(r),
                "offered_by": str(r["offered_by"]) if r["offered_by"] else None,
            }
        )
    return out
