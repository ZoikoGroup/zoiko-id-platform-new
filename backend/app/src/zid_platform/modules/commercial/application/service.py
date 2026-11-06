"""Entitlement gate and the catalogue/entitlement views (ZID-COM-001 §2, §6, §29; CR-024)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_errors import ZidError

from zid_platform.modules.commercial.domain.catalogue import (
    DEFAULT_PLAN,
    ENTITLEMENT_UNAVAILABLE,
    Enforcement,
    Entitlement,
    decide,
)
from zid_platform.modules.commercial.domain.invoicing import NEW_PAID_EXTRAS, restricted
from zid_platform.modules.commercial.infrastructure import catalogue_store as store
from zid_platform.modules.commercial.infrastructure.enforcement_store import company_mode

log = logging.getLogger("zid.commercial")


def _money(v: Decimal | None) -> str | None:
    return None if v is None else format(v.normalize(), "f")


async def entitled(
    conn: AsyncConnection,
    capability: str,
    *,
    used: int | None = None,
    quantity: int = 1,
    enforcement: Enforcement = "shadow",
    now: datetime | None = None,
) -> Entitlement:
    """May this tenant (the connection's RLS scope) use `quantity` more of `capability`?

    Fails closed for the paid extra only: if the entitlement cannot be read, `enforce` mode refuses and the
    other modes allow and log. Callers are paid-extra creation paths, never security paths."""
    now = now or datetime.now(UTC)
    enforcement = await company_mode(conn, enforcement)  # type: ignore[assignment]
    try:
        async with conn.begin_nested():
            current = await store.active_entitlement(conn)
            plan_code = str(current["plan_code"]) if current else DEFAULT_PLAN
            state = str(current["state"]) if current else "ACTIVE"
            rule = await store.plan_capability(conn, plan_code, capability)
            ovr = await store.overrides(conn, now)
            billing = (
                await conn.execute(
                    text("SELECT state, safety_hold FROM commercial.billing_account_state")
                )
            ).first()
    except Exception:  # an unreadable entitlement must not become a grant in enforce mode
        log.exception("entitlement lookup failed for %s", capability)
        denied = enforcement == "enforce"
        return Entitlement(
            capability,
            not denied,
            False,
            ENTITLEMENT_UNAVAILABLE,
            None,
            None,
            used,
            False,
            enforcement,
            DEFAULT_PLAN,
            shadow_denied=not denied,
        )
    result = decide(
        capability,
        plan_code=plan_code,
        rule=rule,
        overrides=ovr,
        used=used,
        quantity=quantity,
        now=now,
        enforcement=enforcement,
        state=state,
        billing_restricted=billing is not None
        and NEW_PAID_EXTRAS in restricted(str(billing[0]), bool(billing[1])),
    )
    if result.shadow_denied:
        log.warning(
            "entitlement shadow denial: capability=%s reason=%s plan=%s",
            capability,
            result.reason,
            plan_code,
        )
    return result


async def request_limit(conn: AsyncConnection, base: int) -> int:
    """This company's request allowance per minute (the connection's RLS scope): its plan's multiple of `base`; 0 = unlimited.

    An unreadable plan gets the base allowance (the limit is a courtesy to other companies, never a reason to refuse a call)."""
    from zid_platform.modules.commercial.domain.throughput import request_limit as limit_for
    from zid_platform.modules.commercial.infrastructure.bands import plan_throughput

    try:
        async with conn.begin_nested():
            current = await store.active_entitlement(conn)
        plan_code = str(current["plan_code"]) if current else DEFAULT_PLAN
    except Exception:
        log.exception("plan lookup for the request limit failed")
        plan_code = DEFAULT_PLAN
    return limit_for(plan_code, base, plan_throughput())


async def production_environments_check(
    conn: AsyncConnection, *, used: int, enforcement: Enforcement = "shadow"
) -> tuple[bool, str, int | None]:
    """May this tenant (the connection's RLS scope) have one more production environment? Returns (allowed, plan code, the plan's limit).

    The limit is the plan's own `production_environments`; a plan with none stated (Enterprise: by contract) has no limit. In `shadow` a
    refusal is logged and allowed, in `enforce` it is refused; an unreadable plan is allowed and logged, never refused (a paid extra, not a
    security path)."""
    enforcement = await company_mode(conn, enforcement)  # type: ignore[assignment]
    try:
        async with conn.begin_nested():
            current = await store.active_entitlement(conn)
            plan_code = str(current["plan_code"]) if current else DEFAULT_PLAN
            limit = (
                await conn.execute(
                    text("SELECT production_environments FROM commercial.plan WHERE plan_code = :p"), {"p": plan_code}
                )
            ).scalar()
    except Exception:
        log.exception("production environment limit lookup failed")
        return True, DEFAULT_PLAN, None
    if limit is None or used < int(limit):
        return True, plan_code, None if limit is None else int(limit)
    if enforcement != "enforce":
        log.warning("plan shadow denial: production_environments used=%s limit=%s plan=%s", used, limit, plan_code)
        return True, plan_code, int(limit)
    return False, plan_code, int(limit)


async def entitlement_view(
    conn: AsyncConnection, *, used: dict[str, int] | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """The tenant's plan, limits, capabilities and the counts behind its caps."""
    now = now or datetime.now(UTC)
    current = await store.active_entitlement(conn)
    plan_code = str(current["plan_code"]) if current else DEFAULT_PLAN
    plan = await store.plan(conn, plan_code)
    caps = await store.plan_capabilities(conn, plan_code)
    ovr = await store.overrides(conn, now)
    used = used or {}
    extra_by_key: dict[str, int] = {}
    granted: set[str] = set()
    for o in ovr:
        extra_by_key[o.capability_key] = extra_by_key.get(o.capability_key, 0) + (
            o.extra_count or 0
        )
        if o.grant_mode:
            granted.add(o.capability_key)
    capabilities = []
    for c in caps:
        counted = c.included_count is not None or extra_by_key.get(c.key, 0) > 0
        capabilities.append(
            {
                "capability": c.key,
                "mode": c.mode,
                "tier": c.tier,
                "released": c.released,
                "granted_by_exception": c.key in granted,
                "limit": (c.included_count or 0) + extra_by_key.get(c.key, 0) if counted else None,
                "used": used.get(c.key) if counted else None,
            }
        )
    assert plan is not None
    return {
        "plan": plan_code,
        "plan_label": plan["label"],
        "state": current["state"] if current else "ACTIVE",
        "source": current["source"] if current else "DEFAULT",
        "effective_from": current["effective_from"].isoformat() if current else None,
        "version": current["version"] if current else 0,
        "limits": {
            "mah_included": plan["mah_included"],
            "auth_events_included": plan["auth_events_included"],
            "policy_decisions_included": plan["policy_decisions_included"],
            "m2m_grants_included": plan["m2m_grants_included"],
            "sso_included": plan["sso_included"],
            "production_environments": plan["production_environments"],
            "support_level": plan["support_level"],
        },
        "capabilities": capabilities,
        "note": "A null limit means negotiated or not capped here; metered volumes are billed, not capped.",
    }


async def catalogue_view(conn: AsyncConnection, now: datetime | None = None) -> dict[str, Any]:
    """The public catalogue: published plans, released capabilities and APPROVED prices only.

    A DRAFT price book is never visible. With no approved book the catalogue lists plans and capabilities but
    `pricing` is null: nothing is priced until a second operator approves a book."""
    now = now or datetime.now(UTC)
    plans = await store.plans(conn)
    book = await store.approved_price_book(conn, now)
    pricing = None
    if book is not None:
        pricing = {
            "version": book["version"],
            "currency": book["currency"],
            "effective_from": book["effective_from"].isoformat(),
            "lines": [
                {
                    "sku": ln["sku_id"],
                    "plan": ln["plan_code"],
                    "unit": ln["unit"],
                    "unit_rate": _money(ln["unit_rate"]),
                    "unit_divisor": ln["unit_divisor"],
                    "released": bool(ln["released"]),
                }
                for ln in await store.lines(conn, str(book["price_book_id"]))
                if ln["released"]
            ],
        }
    out_plans = []
    for p in plans:
        caps = [c for c in await store.plan_capabilities(conn, p["plan_code"]) if c.released]
        out_plans.append(
            {
                "plan": p["plan_code"],
                "label": p["label"],
                "platform_fee_is_floor": p["fee_is_floor"],
                "limits": {
                    "mah_included": p["mah_included"],
                    "auth_events_included": p["auth_events_included"],
                    "policy_decisions_included": p["policy_decisions_included"],
                    "m2m_grants_included": p["m2m_grants_included"],
                    "sso_included": p["sso_included"],
                    "production_environments": p["production_environments"],
                    "support_level": p["support_level"],
                },
                "capabilities": [
                    {"capability": c.key, "mode": c.mode, "tier": c.tier} for c in caps
                ],
            }
        )
    return {
        "status": "PRICED" if pricing else "UNPRICED",
        "plans": out_plans,
        "pricing": pricing,
        "note": "Rates are shown only from an approved price book. Taxes and supplier checks are excluded.",
    }


async def estimate(
    conn: AsyncConnection,
    *,
    plan_code: str,
    usage: dict[str, Decimal],
    connections: dict[str, int],
    now: datetime | None = None,
) -> dict[str, Any]:
    """A non-binding monthly estimate for the public pricing calculator (ZID-COM-001 §19).

    It runs the same `rate` function as the invoice, over the approved price book and only released price lines.
    It reads no tenant data and returns UNPRICED until a price book is approved; it is never a quotation."""
    from zid_platform.modules.commercial.application import rating_service
    from zid_platform.modules.commercial.domain.rating import (
        PriceLine,
        RatingError,
        Segment,
        rate,
    )

    now = now or datetime.now(UTC)
    book = await store.approved_price_book(conn, now)
    if book is None:
        return {"status": "UNPRICED", "assumptions": []}
    plans = {p["plan_code"]: p for p in await store.plans(conn)}
    if plan_code not in plans:
        raise ZidError("PLAN_UNKNOWN", "No such plan.", http_status=422)
    lines = {
        ln["sku_id"]: PriceLine(
            Decimal(ln["unit_rate"]),
            int(ln["unit_divisor"]),
            Decimal(ln["minimum_amount"]) if ln["minimum_amount"] is not None else None,
        )
        for ln in await store.lines(conn, str(book["price_book_id"]))
        if ln["plan_code"] == plan_code and ln["released"]
    }
    start, end = rating_service.bounds(now.date().replace(day=1))
    try:
        terms = await rating_service.terms_for(conn, plan_code)
        rated = rate(
            currency=book["currency"],
            period_start=start,
            period_end=end,
            segments=[Segment(terms, start, end, lines)],
            usage=[usage],
            connections=connections,
        )
    except RatingError as e:
        raise ZidError(e.code, e.detail or e.code, http_status=422) from e
    return {
        "status": "ESTIMATE",
        "plan": plan_code,
        "price_book_version": book["version"],
        "currency": book["currency"],
        "monthly_estimate": _money(rated.subtotal),
        "fee_is_floor": bool(plans[plan_code]["fee_is_floor"]),
        "lines": [
            {"kind": ln.kind, "meter": ln.meter, "formula": ln.formula, "net": _money(ln.net)}
            for ln in rated.lines
        ],
        "warnings": list(rated.warnings),
        "assumptions": [
            "An estimate, not a quotation or an invoice.",
            "Taxes and third-party verification checks (identity, business, screening, messaging) are excluded.",
            "Enterprise pricing is negotiated; the platform fee shown is a starting point only.",
            "Rates come from the approved price book and may change for new orders.",
        ],
    }
