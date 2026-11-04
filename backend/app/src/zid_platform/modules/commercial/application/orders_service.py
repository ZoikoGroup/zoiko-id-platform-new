"""Accounts, quotes, approvals and orders (ZID-COM-001 §10, §12, §14, §17; CR-028).

Operator-plane records. The only path from an order to a tenant's entitlement is `activate()`, which is
idempotent on the order and needs a different person from the one who entered the order.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import in_tenant

from zid_platform.modules.commercial.application import commitment_service as commitments
from zid_platform.modules.commercial.application import events, vendor_service
from zid_platform.modules.commercial.application import finance_service as finance
from zid_platform.modules.commercial.application.change_log import log_change
from zid_platform.modules.commercial.domain import orders as rules
from zid_platform.modules.commercial.infrastructure import catalogue_store
from zid_platform.modules.commercial.infrastructure.finance_config import finance_policy

QUOTE_VALIDITY = timedelta(days=30)
CONNECTION_SKUS = {"ZID-FED": "saml_sso", "ZID-SCIM": "scim"}


def _d(v: Any) -> str | None:
    return None if v is None else format(Decimal(v).normalize(), "f")


# ------------------------------------------------------------------------------- accounts


async def create_account(
    conn: AsyncConnection,
    *,
    legal_name: str,
    kind: str,
    country: str | None,
    currency: str,
    reseller_of: str | None,
    actor: str,
) -> str:
    aid = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO commercial.account (account_id, legal_name, country, currency, kind, reseller_of, created_by) "
            "VALUES (:a, :n, :c, :cur, :k, :r, :b)"
        ),
        {
            "a": aid,
            "n": legal_name,
            "c": country,
            "cur": currency,
            "k": kind,
            "r": uuid.UUID(reseller_of) if reseller_of else None,
            "b": uuid.UUID(actor),
        },
    )
    return str(aid)


async def link_tenant(
    conn: AsyncConnection, account_id: str, tenant_id: str, now: datetime | None = None
) -> None:
    """A tenant has exactly one active account; moving it ends the previous link (history stays)."""
    now = now or datetime.now(UTC)
    await conn.execute(
        text(
            "UPDATE commercial.account_tenant SET effective_to = :n WHERE tenant_id = :t AND effective_to IS NULL"
        ),
        {"n": now, "t": uuid.UUID(tenant_id)},
    )
    await conn.execute(
        text(
            "INSERT INTO commercial.account_tenant (account_id, tenant_id, effective_from) VALUES (:a, :t, :n)"
        ),
        {"a": uuid.UUID(account_id), "t": uuid.UUID(tenant_id), "n": now},
    )


async def account_of(conn: AsyncConnection, tenant_id: str) -> str | None:
    v = (
        await conn.execute(
            text(
                "SELECT account_id FROM commercial.account_tenant WHERE tenant_id = :t AND effective_to IS NULL"
            ),
            {"t": uuid.UUID(tenant_id)},
        )
    ).scalar()
    return str(v) if v else None


async def holds_role(conn: AsyncConnection, principal: str, role: str) -> bool:
    return bool(
        (
            await conn.execute(
                text(
                    "SELECT 1 FROM commercial.commercial_role_holder WHERE principal_id = :p AND role = :r "
                    "AND revoked_at IS NULL AND effective_from <= now() "
                    "AND (effective_to IS NULL OR effective_to > now())"
                ),
                {"p": uuid.UUID(principal), "r": role},
            )
        ).first()
    )


# ------------------------------------------------------------------------------- quotes


async def _catalogue(conn: AsyncConnection, price_book_id: str) -> rules.Catalogue:
    skus = (
        await conn.execute(text("SELECT sku_id, consumption, released FROM commercial.sku"))
    ).all()
    prices = {
        (ln["sku_id"], ln["plan_code"]): Decimal(ln["unit_rate"])
        for ln in await catalogue_store.lines(conn, price_book_id)
    }
    regions = (
        await conn.execute(
            text("SELECT region FROM commercial.region_release WHERE sre_gate_passed")
        )
    ).scalars()
    floors = (
        await conn.execute(text("SELECT sku_id, region, floor_price FROM commercial.price_floor"))
    ).all()
    return rules.Catalogue(
        released_skus=frozenset(r[0] for r in skus if r[2]),
        sku_consumption={r[0]: r[1] for r in skus},
        prices=prices,
        released_regions=frozenset(regions),
        floors={(r[0], r[1]): Decimal(r[2]) for r in floors},
        confirmed_vendor_skus=await vendor_service.confirmed_skus(conn),
    )


async def _bands(conn: AsyncConnection) -> list[rules.Band]:
    rows = (
        await conn.execute(text("SELECT band_max, approvers FROM commercial.discount_policy"))
    ).all()
    return [rules.Band(Decimal(r[0]) if r[0] is not None else None, frozenset(r[1])) for r in rows]


async def create_quote(
    conn: AsyncConnection,
    *,
    account_id: str,
    price_book_id: str,
    region: str,
    term_months: int,
    lines: Sequence[rules.QuoteLine],
    prepaid: bool,
    prepaid_discount: Decimal,
    liability_capped: bool,
    residency_commitment: str | None,
    pilot: bool,
    actor: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    book = await catalogue_store.price_book(conn, price_book_id)
    if book is None:
        raise rules.OrderError("PRICE_BOOK_NOT_FOUND")
    account = (
        await conn.execute(
            text("SELECT currency, risk_status FROM commercial.account WHERE account_id = :a"),
            {"a": uuid.UUID(account_id)},
        )
    ).first()
    if account is None:
        raise rules.OrderError("ACCOUNT_NOT_FOUND")
    if account[1] == "BLOCKED":
        raise rules.OrderError("ACCOUNT_BLOCKED")
    quote = rules.Quote(
        lines=tuple(lines),
        region=region,
        currency=book["currency"],
        price_book_status=book["status"],
        price_book_effective_from=book["effective_from"],
        quoted_at=now,
        prepaid=prepaid,
        prepaid_discount_pct=prepaid_discount,
        liability_capped=liability_capped,
        residency_commitment=residency_commitment,
    )
    v = rules.validate(quote, await _catalogue(conn, price_book_id), await _bands(conn))
    payload = {
        "account": account_id,
        "book": price_book_id,
        "region": region,
        "term": term_months,
        "prepaid": [prepaid, str(prepaid_discount)],
        "liability_capped": liability_capped,
        "residency": residency_commitment,
        "pilot": pilot,
        "lines": [
            [ln.seq, ln.sku, ln.plan_code, str(ln.quantity), str(ln.discount_pct)] for ln in lines
        ],
    }
    qid = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO commercial.quote (quote_id, account_id, price_book_id, currency, region, term_months, prepaid, "
            "prepaid_discount, liability_capped, residency_commitment, pilot, status, reasons, approvers_required, "
            "digest, valid_until, created_by) VALUES (:q, :a, :b, :c, :r, :t, :p, :pd, :lc, :rc, :pi, :s, :rs, :ap, "
            ":d, :v, :by)"
        ),
        {
            "q": qid,
            "a": uuid.UUID(account_id),
            "b": uuid.UUID(price_book_id),
            "c": book["currency"],
            "r": region,
            "t": term_months,
            "p": prepaid,
            "pd": prepaid_discount,
            "lc": liability_capped,
            "rc": residency_commitment,
            "pi": pilot,
            "s": v.status,
            "rs": list(v.reasons),
            "ap": sorted(v.approvers_required),
            "d": rules.digest(payload),
            "v": now + QUOTE_VALIDITY,
            "by": uuid.UUID(actor),
        },
    )
    priced = {int(p["seq"]): p for p in v.lines}
    for ln in lines:
        p = priced.get(ln.seq)
        await conn.execute(
            text(
                "INSERT INTO commercial.quote_line (quote_id, seq, sku_id, plan_code, quantity, discount_pct, list_price, "
                "net_price) VALUES (:q, :s, :k, :p, :n, :d, :lp, :np)"
            ),
            {
                "q": qid,
                "s": ln.seq,
                "k": ln.sku,
                "p": ln.plan_code,
                "n": ln.quantity,
                "d": ln.discount_pct,
                "lp": Decimal(p["list_unit_price"]) if p else None,
                "np": Decimal(p["net_unit_price"]) if p else None,
            },
        )
    return await quote_view(conn, str(qid))


async def quote_view(conn: AsyncConnection, quote_id: str) -> dict[str, Any]:
    q = (
        (
            await conn.execute(
                text("SELECT * FROM commercial.quote WHERE quote_id = :q"),
                {"q": uuid.UUID(quote_id)},
            )
        )
        .mappings()
        .first()
    )
    if q is None:
        raise rules.OrderError("QUOTE_NOT_FOUND")
    lines = (
        await conn.execute(
            text("SELECT * FROM commercial.quote_line WHERE quote_id = :q ORDER BY seq"),
            {"q": uuid.UUID(quote_id)},
        )
    ).mappings()
    approvals = (
        await conn.execute(
            text(
                "SELECT role, approver FROM commercial.quote_approval WHERE quote_id = :q AND digest = :d"
            ),
            {"q": uuid.UUID(quote_id), "d": q["digest"]},
        )
    ).all()
    return {
        "quote_id": str(q["quote_id"]),
        "account_id": str(q["account_id"]),
        "status": q["status"],
        "reasons": list(q["reasons"]),
        "approvers_required": list(q["approvers_required"]),
        "approved_by_role": sorted(r[0] for r in approvals),
        "region": q["region"],
        "currency": q["currency"],
        "term_months": q["term_months"],
        "pilot": q["pilot"],
        "digest": q["digest"],
        "valid_until": q["valid_until"].isoformat(),
        "lines": [
            {
                "seq": ln["seq"],
                "sku": ln["sku_id"],
                "plan": ln["plan_code"],
                "quantity": _d(ln["quantity"]),
                "discount_pct": _d(ln["discount_pct"]),
                "list_price": _d(ln["list_price"]),
                "net_price": _d(ln["net_price"]),
            }
            for ln in lines
        ],
    }


async def approve_quote(
    conn: AsyncConnection,
    quote_id: str,
    *,
    role: str,
    approver: str,
    now: datetime | None = None,
    direct: bool = False,
    policy_lock: bool = False,
) -> dict[str, Any]:
    """Record one role's approval of this exact quote digest; the quote becomes APPROVED when every required
    role is held by a different person, none of them its author. Direct mode (one operator runs the platform)
    approves every required role at once as the direct approver; a hard pricing reason still blocks the quote.
    With `policy_lock` (a deployed environment) the discount bands have authority only while the Founder and the
    CFO have signed the finance policy (ZID-COM-CL-004, slice L-14b); direct mode, whose one operator cannot give two
    signatures, is its own explicitly acknowledged stand-in and is not locked."""
    now = now or datetime.now(UTC)
    q = (
        (
            await conn.execute(
                text("SELECT * FROM commercial.quote WHERE quote_id = :q FOR UPDATE"),
                {"q": uuid.UUID(quote_id)},
            )
        )
        .mappings()
        .first()
    )
    if q is None:
        raise rules.OrderError("QUOTE_NOT_FOUND")
    if q["status"] != "NEEDS_APPROVAL":
        raise rules.OrderError("QUOTE_NOT_AWAITING_APPROVAL")
    if q["valid_until"] < now:
        raise rules.OrderError("QUOTE_EXPIRED")
    if role not in q["approvers_required"]:
        raise rules.OrderError("APPROVAL_ROLE_NOT_REQUIRED")
    if policy_lock and not direct and not (await finance.policy_state(conn))["effective"]:
        raise rules.OrderError(
            "POLICY_NOT_SIGNED",
            "the finance policy is not signed by the Founder and the CFO, so the discount bands have no authority",
        )
    if not direct:
        if str(q["created_by"]) == approver:
            raise rules.OrderError(
                "QUOTE_SELF_APPROVAL", "the author never approves their own quote"
            )
        if not await holds_role(conn, approver, role):
            raise rules.OrderError("APPROVER_ROLE_MISSING", f"the approver does not hold {role}")
    for given_role in sorted(q["approvers_required"]) if direct else [role]:
        await conn.execute(
            text(
                "INSERT INTO commercial.quote_approval (quote_id, role, approver, digest) VALUES (:q, :r, :a, :d) "
                "ON CONFLICT (quote_id, role) DO NOTHING"
            ),
            {"q": uuid.UUID(quote_id), "r": given_role, "a": uuid.UUID(approver), "d": q["digest"]},
        )
    given = {
        r[0]: str(r[1])
        for r in (
            await conn.execute(
                text(
                    "SELECT role, approver FROM commercial.quote_approval WHERE quote_id = :q AND digest = :d"
                ),
                {"q": uuid.UUID(quote_id), "d": q["digest"]},
            )
        ).all()
    }
    missing = (
        frozenset()
        if direct
        else rules.missing_approvals(
            frozenset(q["approvers_required"]), given, str(q["created_by"])
        )
    )
    hard = any(rules.REASONS.get(r) == rules.HARD for r in q["reasons"])
    if not missing and not hard:
        await conn.execute(
            text("UPDATE commercial.quote SET status = 'APPROVED' WHERE quote_id = :q"),
            {"q": uuid.UUID(quote_id)},
        )
    return {**(await quote_view(conn, quote_id)), "missing_roles": sorted(missing)}


# ------------------------------------------------------------------------------- orders


async def accept(
    conn: AsyncConnection,
    quote_id: str,
    *,
    tenant_id: str,
    signer_ref: str,
    signed_document_digest: str,
    effective_from: datetime,
    amends_order_id: str | None,
    actor: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The buyer signed the quote: it becomes an order for one of the account's tenants."""
    now = now or datetime.now(UTC)
    q = (
        (
            await conn.execute(
                text("SELECT * FROM commercial.quote WHERE quote_id = :q FOR UPDATE"),
                {"q": uuid.UUID(quote_id)},
            )
        )
        .mappings()
        .first()
    )
    if q is None:
        raise rules.OrderError("QUOTE_NOT_FOUND")
    if q["status"] not in ("VALIDATED", "APPROVED"):
        raise rules.OrderError("QUOTE_NOT_ACCEPTABLE", f"a {q['status']} quote cannot be signed")
    if q["valid_until"] < now:
        raise rules.OrderError("QUOTE_EXPIRED")
    if await account_of(conn, tenant_id) != str(q["account_id"]):
        raise rules.OrderError(
            "TENANT_NOT_IN_ACCOUNT", "the order's tenant must belong to the quoted account"
        )
    lines = (
        (
            await conn.execute(
                text("SELECT * FROM commercial.quote_line WHERE quote_id = :q ORDER BY seq"),
                {"q": uuid.UUID(quote_id)},
            )
        )
        .mappings()
        .all()
    )
    plan = next((ln["plan_code"] for ln in lines if ln["sku_id"] == "ZID-BASE"), None)
    if plan is None:
        raise rules.OrderError("PLAN_LINE_MISSING")
    oid = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO commercial.sales_order (order_id, account_id, quote_id, tenant_id, plan_code, price_book_id, "
            "signer_ref, signed_document_digest, effective_from, term_months, amends_order_id, pilot, pilot_ends_at, "
            "status, created_by) VALUES (:o, :a, :q, :t, :p, :b, :s, :d, :e, :tm, :am, :pi, :pe, 'SIGNED', :by)"
        ),
        {
            "o": oid,
            "a": q["account_id"],
            "q": uuid.UUID(quote_id),
            "t": uuid.UUID(tenant_id),
            "p": plan,
            "b": q["price_book_id"],
            "s": signer_ref,
            "d": signed_document_digest,
            "e": effective_from,
            "tm": q["term_months"],
            "am": uuid.UUID(amends_order_id) if amends_order_id else None,
            "pi": q["pilot"],
            "pe": effective_from + timedelta(days=30 * int(q["term_months"]))
            if q["pilot"]
            else None,
            "by": uuid.UUID(actor),
        },
    )
    for ln in lines:
        await conn.execute(
            text(
                "INSERT INTO commercial.sales_order_line (order_id, seq, sku_id, plan_code, quantity, net_price) "
                "VALUES (:o, :s, :k, :p, :n, :np)"
            ),
            {
                "o": oid,
                "s": ln["seq"],
                "k": ln["sku_id"],
                "p": ln["plan_code"],
                "n": ln["quantity"],
                "np": ln["net_price"],
            },
        )
    await conn.execute(
        text("UPDATE commercial.quote SET status = 'ACCEPTED' WHERE quote_id = :q"),
        {"q": uuid.UUID(quote_id)},
    )
    return await order_view(conn, str(oid))


async def activate(
    conn: AsyncConnection,
    order_id: str,
    *,
    actor: str,
    now: datetime | None = None,
    direct: bool = False,
) -> dict[str, Any]:
    """The one route from a signed order to the tenant's entitlement. Idempotent: a second call changes
    nothing. The activator is never the person who entered the order."""
    now = now or datetime.now(UTC)
    o = (
        (
            await conn.execute(
                text("SELECT * FROM commercial.sales_order WHERE order_id = :o FOR UPDATE"),
                {"o": uuid.UUID(order_id)},
            )
        )
        .mappings()
        .first()
    )
    if o is None:
        raise rules.OrderError("ORDER_NOT_FOUND")
    if o["status"] == "ACTIVATED":
        return {**(await order_view(conn, order_id)), "already_active": True}
    if o["status"] != "SIGNED":
        raise rules.OrderError("ORDER_NOT_SIGNED")
    if not direct and str(o["created_by"]) == actor:
        raise rules.OrderError("ORDER_SELF_ACTIVATION", "a second person activates the order")
    tenant = str(o["tenant_id"])
    mode = finance_policy().commitment.enforcement
    if (
        mode != "OFF"
    ):  # the customer's own authority approved this obligation (ZID-COM-CL-004 section 04), or the owners' switch says what happens
        problem = await commitments.activation_problem(
            conn, order_id=order_id, tenant_id=tenant, now=now
        )
        if problem is not None:
            if mode == "ENFORCED":
                raise rules.OrderError("COMMITMENT_REQUIRED", problem)
            await log_change(
                conn,
                kind="COMMITMENT_UNAPPROVED_ACTIVATION",
                ref=order_id,
                tenant_id=tenant,
                actor=actor,
                reason=problem,
            )
    lines = (
        await conn.execute(
            text("SELECT sku_id, quantity FROM commercial.sales_order_line WHERE order_id = :o"),
            {"o": uuid.UUID(order_id)},
        )
    ).all()
    start = max(o["effective_from"], now)
    async with in_tenant(conn, tenant):
        result = await catalogue_store.set_entitlement(
            conn,
            tenant_id=tenant,
            plan_code=o["plan_code"],
            price_book_id=str(o["price_book_id"]),
            actor=actor,
            now=start,
            source="ORDER",
            source_order_id=order_id,
        )
        for sku, qty in lines:
            capability = CONNECTION_SKUS.get(sku)
            if capability:
                await catalogue_store.add_override(
                    conn,
                    tenant_id=tenant,
                    capability_key=capability,
                    grant_mode="ADD_ON",
                    extra_count=int(qty),
                    reason=f"order {order_id}",
                    approved_by=str(o["created_by"]),
                    created_by=actor,
                    expires_at=o["pilot_ends_at"],
                )
        await events.entitlement_changed(
            conn,
            tenant_id=tenant,
            entitlement_id=result["entitlement_id"],
            version=int(result["version"]),
            plan=o["plan_code"],
            previous_plan=result["previous_plan"],
            price_book_version=None,
            effective_from=start,
        )
    if o["amends_order_id"]:
        await conn.execute(
            text(
                "UPDATE commercial.sales_order SET status = 'SUPERSEDED' WHERE order_id = :o AND status = 'ACTIVATED'"
            ),
            {"o": o["amends_order_id"]},
        )
    await conn.execute(
        text(
            "UPDATE commercial.sales_order SET status = 'ACTIVATED', activated_at = :n, activated_by = :a "
            "WHERE order_id = :o"
        ),
        {"n": now, "a": uuid.UUID(actor), "o": uuid.UUID(order_id)},
    )
    await log_change(
        conn,
        kind="ORDER_ACTIVATED",
        ref=order_id,
        tenant_id=tenant,
        actor=actor,
        detail={"plan": o["plan_code"], "price_book_id": str(o["price_book_id"])},
    )
    return {
        **(await order_view(conn, order_id)),
        "already_active": False,
        "entitlement_id": result["entitlement_id"],
    }


async def order_view(conn: AsyncConnection, order_id: str) -> dict[str, Any]:
    o = (
        (
            await conn.execute(
                text("SELECT * FROM commercial.sales_order WHERE order_id = :o"),
                {"o": uuid.UUID(order_id)},
            )
        )
        .mappings()
        .first()
    )
    if o is None:
        raise rules.OrderError("ORDER_NOT_FOUND")
    lines = (
        await conn.execute(
            text("SELECT * FROM commercial.sales_order_line WHERE order_id = :o ORDER BY seq"),
            {"o": uuid.UUID(order_id)},
        )
    ).mappings()
    return {
        "order_id": str(o["order_id"]),
        "account_id": str(o["account_id"]),
        "tenant_id": str(o["tenant_id"]),
        "plan": o["plan_code"],
        "status": o["status"],
        "pilot": o["pilot"],
        "pilot_ends_at": o["pilot_ends_at"].isoformat() if o["pilot_ends_at"] else None,
        "effective_from": o["effective_from"].isoformat(),
        "term_months": o["term_months"],
        "amends_order_id": str(o["amends_order_id"]) if o["amends_order_id"] else None,
        "activated_at": o["activated_at"].isoformat() if o["activated_at"] else None,
        "lines": [
            {
                "seq": ln["seq"],
                "sku": ln["sku_id"],
                "plan": ln["plan_code"],
                "quantity": _d(ln["quantity"]),
                "net_price": _d(ln["net_price"]),
            }
            for ln in lines
        ],
    }


async def orders_of_tenant(conn: AsyncConnection, tenant_id: str) -> list[dict[str, Any]]:
    """A tenant's own orders, by its server-derived id; never another tenant's or account's."""
    ids = (
        await conn.execute(
            text(
                "SELECT order_id FROM commercial.sales_order WHERE tenant_id = :t ORDER BY created_at DESC"
            ),
            {"t": uuid.UUID(tenant_id)},
        )
    ).scalars()
    return [await order_view(conn, str(i)) for i in ids]
