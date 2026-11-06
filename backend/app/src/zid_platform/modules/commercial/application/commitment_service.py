"""Customer commitment approval in motion (ZID-COM-CL-004 section 04, section 07; slice L-14c, CR-054).

A signed order is a new contractual obligation of the customer organization. The server works out its exposure from the order and quote (a caller never
sends an amount), places it in a tier, and records what the customer's own authority must sign: the Owner, a Billing Admin the Owner gave a spend limit, a
Procurement Approver the Owner named, or two people above the top tier. Nonstandard terms also need a Zoiko contract, legal and finance review mark before
any customer signature counts. Authority is read afresh at every signature and again when the order is activated, so a revoked limit has no effect left.

Commercial does not decide who is an Owner: the tenant module owns roles and hands its answer in at start-up (`configure_authority`). Until it does, nobody
is an Owner here and nothing can be approved; the system fails closed.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import in_tenant

from zid_platform.modules.commercial.application import finance_service as finance
from zid_platform.modules.commercial.application.change_log import log_change
from zid_platform.modules.commercial.domain import commitment as rules
from zid_platform.modules.commercial.domain import finance as fin_rules
from zid_platform.modules.commercial.domain.commitment import (
    Authority,
    CommitmentError,
    Delegate,
    Tier,
)
from zid_platform.modules.commercial.infrastructure.finance_config import finance_policy

OwnerCheck = Callable[[AsyncConnection, str, str], Awaitable[bool]]
_owner_check: OwnerCheck | None = None
NEEDS = {
    Tier.T1: ["OWNER_OR_BILLING_SPEND"],
    Tier.T2: ["OWNER_OR_PROCUREMENT_APPROVER"],
    Tier.T3: ["OWNER", "PROCUREMENT_APPROVER"],
}


def configure_authority(check: OwnerCheck | None) -> None:
    global _owner_check
    _owner_check = check


async def _is_owner(conn: AsyncConnection, tenant_id: str, principal: str) -> bool:
    return False if _owner_check is None else await _owner_check(conn, tenant_id, principal)


def _json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True)


# ------------------------------------------------------------------------------------------------------------------ delegation


async def delegate(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    grantor: str,
    principal: str,
    kind: str,
    cap_amount: Decimal,
    effective_to: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The Owner gives a spend limit (Billing Admin) or a Procurement Approver role, with a USD limit, dated; never to themselves."""
    now = now or datetime.now(UTC)
    cap = fin_rules.to_minor(cap_amount)
    rules.check_delegation(
        kind,
        cap,
        grantor=grantor,
        grantee=principal,
        is_owner=await _is_owner(conn, tenant_id, grantor),
    )
    if effective_to is not None and effective_to <= now:
        raise CommitmentError("WINDOW_INVALID", "a delegation ends in the future")
    did = uuid.uuid4()
    try:
        await conn.execute(
            text(
                "INSERT INTO commercial.commitment_delegate (delegate_id, tenant_id, principal_id, kind, cap_usd_minor, effective_from, effective_to, granted_by) "
                "VALUES (:d, :t, :p, :k, :c, :f, :e, :g)"
            ),
            {
                "d": did,
                "t": uuid.UUID(tenant_id),
                "p": uuid.UUID(principal),
                "k": kind,
                "c": cap,
                "f": now,
                "e": effective_to,
                "g": uuid.UUID(grantor),
            },
        )
    except Exception as e:
        if "commitment_delegate_live" in str(e):
            raise CommitmentError(
                "ALREADY_DELEGATED", "that person already holds this; take it away first"
            ) from e
        raise
    await log_change(conn, kind="COMMITMENT_DELEGATED", ref=str(did), tenant_id=tenant_id, actor=grantor, reason=kind,
                     detail={"principal": principal, "cap_usd_minor": cap, "to": effective_to.isoformat() if effective_to else None})  # fmt: skip
    return {
        "delegate_id": str(did),
        "principal_id": principal,
        "kind": kind,
        "cap_usd": f"{Decimal(cap) / 100:.2f}",
    }


async def revoke_delegate(
    conn: AsyncConnection, *, tenant_id: str, delegate_id: str, by: str, now: datetime | None = None
) -> None:
    now = now or datetime.now(UTC)
    if not await _is_owner(conn, tenant_id, by):
        raise CommitmentError(
            "OWNER_ONLY", "only the Owner gives or takes away a spend limit or a procurement role"
        )
    row = (
        await conn.execute(
            text(
                "UPDATE commercial.commitment_delegate SET revoked_at = :n, revoked_by = :b WHERE delegate_id = :d AND revoked_at IS NULL RETURNING principal_id, kind"
            ),
            {"d": uuid.UUID(delegate_id), "b": uuid.UUID(by), "n": now},
        )
    ).first()
    if row is None:
        raise CommitmentError("DELEGATE_NOT_FOUND", "no live delegation with that id")
    await log_change(
        conn,
        kind="COMMITMENT_DELEGATE_REVOKED",
        ref=delegate_id,
        tenant_id=tenant_id,
        actor=by,
        reason=row.kind,
        detail={"principal": str(row.principal_id)},
    )


async def delegates(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text("SELECT delegate_id, principal_id, kind, cap_usd_minor, effective_from, effective_to, revoked_at FROM commercial.commitment_delegate ORDER BY created_at DESC")
        )
    ).mappings().all()  # fmt: skip
    return [
        {
            "delegate_id": str(r["delegate_id"]), "principal_id": str(r["principal_id"]), "kind": r["kind"],
            "cap_usd": f"{Decimal(r['cap_usd_minor']) / 100:.2f}", "effective_from": r["effective_from"].isoformat(),
            "effective_to": r["effective_to"].isoformat() if r["effective_to"] else None,
            "revoked_at": r["revoked_at"].isoformat() if r["revoked_at"] else None,
        }
        for r in rows
    ]  # fmt: skip


async def _live_delegate(
    conn: AsyncConnection, tenant_id: str, principal: str, wanted: str, now: datetime
) -> Delegate | None:
    rows = (
        await conn.execute(
            text(
                "SELECT kind, cap_usd_minor FROM commercial.commitment_delegate WHERE tenant_id = :t AND principal_id = :p AND revoked_at IS NULL "
                "AND effective_from <= :n AND (effective_to IS NULL OR effective_to > :n)"
            ),
            {"t": uuid.UUID(tenant_id), "p": uuid.UUID(principal), "n": now},
        )
    ).all()
    held = {r.kind: int(r.cap_usd_minor) for r in rows}
    if wanted in held:
        return Delegate(wanted, held[wanted])
    return Delegate(rows[0].kind, int(rows[0].cap_usd_minor)) if rows else None


# --------------------------------------------------------------------------------------------------------------------- requests


async def _monthly_minor(conn: AsyncConnection, quote_id: Any) -> int:
    rows = (
        await conn.execute(
            text(
                "SELECT quantity, net_price FROM commercial.quote_line WHERE quote_id = :q AND net_price IS NOT NULL"
            ),
            {"q": quote_id},
        )
    ).all()
    return int(
        sum(
            (Decimal(r.quantity) * Decimal(r.net_price) * 100).to_integral_value(
                rounding=ROUND_CEILING
            )
            for r in rows
        )
    )


async def monthly_value_minor(conn: AsyncConnection, quote_id: Any) -> int:
    """The quote's monthly value in minor units: the one figure a company approves as a commitment and pays as its first month (CR-062)."""
    return await _monthly_minor(conn, quote_id)


def _view(r: Any, approvals: list[Any]) -> dict[str, Any]:
    return {
        "request_id": str(r["request_id"]), "order_id": str(r["order_id"]), "currency": r["currency"], "term_months": r["term_months"],
        "monthly": f"{Decimal(r['monthly_minor']) / 100:.2f}", "exposure": f"{Decimal(r['exposure_minor']) / 100:.2f}",
        "exposure_usd_minor": r["exposure_usd_minor"], "tier": r["tier"], "needs": NEEDS[Tier(r["tier"])],
        "review_required": r["review_required"], "reviewed_at": r["reviewed_at"].isoformat() if r["reviewed_at"] else None,
        "snapshot_hash": r["snapshot_hash"], "policy_version": r["policy_version"], "state": r["state"],
        "requested_by": str(r["requested_by"]), "created_at": r["created_at"].isoformat(), "expires_at": r["expires_at"].isoformat(),
        "approved_at": r["approved_at"].isoformat() if r["approved_at"] else None,
        "approval_expires_at": r["approval_expires_at"].isoformat() if r["approval_expires_at"] else None,
        "approvals": [{"authority": a["authority"], "principal_id": str(a["principal_id"]), "decision": a["decision"], "decided_at": a["decided_at"].isoformat()} for a in approvals],
    }  # fmt: skip


async def _load(conn: AsyncConnection, request_id: str, *, lock: bool = False) -> Any:
    row = (
        await conn.execute(
            text("SELECT * FROM commercial.commitment_request WHERE request_id = :r" + (" FOR UPDATE" if lock else "")),
            {"r": uuid.UUID(request_id)},
        )
    ).mappings().first()  # fmt: skip
    if row is None:
        raise CommitmentError("REQUEST_NOT_FOUND", "no such request")
    return row


async def _approvals(conn: AsyncConnection, request_id: str) -> list[Any]:
    return list(
        (
            await conn.execute(
                text("SELECT authority, principal_id, decision, decided_at FROM commercial.commitment_approval WHERE request_id = :r ORDER BY decided_at"),
                {"r": uuid.UUID(request_id)},
            )
        ).mappings().all()
    )  # fmt: skip


async def _lapse(conn: AsyncConnection, r: Any, now: datetime) -> Any:
    late = (r["state"] == "PENDING_APPROVAL" and r["expires_at"] <= now) or (
        r["state"] == "APPROVED"
        and r["approval_expires_at"] is not None
        and r["approval_expires_at"] <= now
    )
    if not late:
        return r
    await conn.execute(
        text("UPDATE commercial.commitment_request SET state = 'EXPIRED' WHERE request_id = :r"),
        {"r": r["request_id"]},
    )
    await log_change(
        conn,
        kind="COMMITMENT_EXPIRED",
        ref=str(r["request_id"]),
        tenant_id=str(r["tenant_id"]),
        actor=None,
        reason=r["state"],
    )
    return await _load(conn, str(r["request_id"]))


async def request(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    order_id: str,
    requested_by: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Ask for the customer's approval of a signed order. The exposure is the server's: from the order's own quote, never from the caller."""
    now = now or datetime.now(UTC)
    policy = finance_policy()
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
    if o is None or str(o["tenant_id"]) != tenant_id:
        raise CommitmentError("ORDER_NOT_FOUND", "no such order for this organization")
    if o["status"] != "SIGNED":
        raise CommitmentError(
            "ORDER_NOT_SIGNED",
            "only a signed order that is not yet active needs a commitment approval",
        )
    q = (
        (
            await conn.execute(
                text("SELECT * FROM commercial.quote WHERE quote_id = :q"), {"q": o["quote_id"]}
            )
        )
        .mappings()
        .first()
    )
    assert q is not None
    live = (
        await conn.execute(
            text(
                "SELECT request_id FROM commercial.commitment_request WHERE order_id = :o AND state IN ('PENDING_APPROVAL', 'APPROVED')"
            ),
            {"o": o["order_id"]},
        )
    ).first()
    if live is not None:  # one live request per order: asking again is the same request
        return await get(conn, str(live.request_id), now=now)
    monthly = await _monthly_minor(conn, o["quote_id"])
    amended = 0
    if o["amends_order_id"] is not None:
        before = (
            await conn.execute(
                text("SELECT quote_id FROM commercial.sales_order WHERE order_id = :o"),
                {"o": o["amends_order_id"]},
            )
        ).first()
        amended = await _monthly_minor(conn, before.quote_id) if before else 0
    exposure = rules.exposure_minor(monthly, int(o["term_months"]), amended)
    usd = fin_rules.usd_minor(exposure, q["currency"], policy.fx_usd)
    tier = rules.tier_for(policy.commitment, usd)
    review = rules.needs_review(
        policy.commitment,
        liability_capped=q["liability_capped"],
        residency_commitment=q["residency_commitment"],
        term_months=int(o["term_months"]),
    )
    snapshot = {
        "order_id": order_id, "quote_digest": q["digest"], "account_id": str(o["account_id"]), "currency": q["currency"], "monthly_minor": monthly,
        "term_months": int(o["term_months"]), "exposure_minor": exposure, "exposure_usd_minor": usd, "tier": tier.value, "review_required": review,
        "policy_version": policy.version, "policy_hash": policy.content_hash,
    }  # fmt: skip
    digest = fin_rules.snapshot_hash(snapshot)
    rid = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO commercial.commitment_request (request_id, tenant_id, order_id, quote_digest, account_id, currency, monthly_minor, term_months, "
            "exposure_minor, exposure_usd_minor, tier, review_required, snapshot, snapshot_hash, policy_version, policy_hash, state, requested_by, created_at, expires_at) "
            "VALUES (:id, :t, :o, :qd, :a, :c, :m, :tm, :x, :u, :tier, :rv, CAST(:s AS jsonb), :h, :pv, :ph, 'PENDING_APPROVAL', :by, :now, :exp)"
        ),
        {
            "id": rid, "t": uuid.UUID(tenant_id), "o": o["order_id"], "qd": q["digest"], "a": o["account_id"], "c": q["currency"], "m": monthly,
            "tm": int(o["term_months"]), "x": exposure, "u": usd, "tier": tier.value, "rv": review, "s": _json(snapshot), "h": digest,
            "pv": policy.version, "ph": policy.content_hash, "by": uuid.UUID(requested_by), "now": now,
            "exp": now + timedelta(days=policy.commitment.valid_days),
        },
    )  # fmt: skip
    await log_change(conn, kind="COMMITMENT_REQUESTED", ref=str(rid), tenant_id=tenant_id, actor=requested_by, reason=tier.value,
                     detail={"order_id": order_id, "exposure_usd_minor": usd, "review_required": review, "snapshot_hash": digest})  # fmt: skip
    return _view(await _load(conn, str(rid)), [])


async def get(
    conn: AsyncConnection, request_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    r = await _lapse(conn, await _load(conn, request_id, lock=True), now or datetime.now(UTC))
    return _view(r, await _approvals(conn, request_id))


async def list_requests(conn: AsyncConnection, *, limit: int = 100) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT * FROM commercial.commitment_request ORDER BY created_at DESC LIMIT :n"
                ),
                {"n": limit},
            )
        )
        .mappings()
        .all()
    )
    return [_view(r, []) for r in rows]


async def decide(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    request_id: str,
    approver: str,
    decision: str,
    presented_hash: str,
    reason: str | None,
    step_up_at: datetime | None,
    environment: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    if decision not in ("APPROVE", "REJECT"):
        raise CommitmentError("DECISION_INVALID", decision)
    r = await _lapse(conn, await _load(conn, request_id, lock=True), now)
    if r["state"] != "PENDING_APPROVAL":
        raise CommitmentError("NOT_PENDING", f"a request that is {r['state']} is not decided")
    try:
        await finance.require_effective(conn, environment)
    except fin_rules.FinanceError as e:  # the customer's approval tiers have no authority until the policy is signed (ZID-UX-COM-008 UI-13)
        raise CommitmentError(e.code, e.detail) from e
    decided = {
        str(a["principal_id"]): a["authority"]
        for a in await _approvals(conn, request_id)
        if a["decision"] == "APPROVE"
    }
    tier = Tier(r["tier"])
    wanted = (
        Authority.BILLING_SPEND.value if tier is Tier.T1 else Authority.PROCUREMENT_APPROVER.value
    )
    authority = rules.check_decision(
        tier, approver=approver, requester=str(r["requested_by"]), is_owner=await _is_owner(conn, tenant_id, approver),
        delegate=await _live_delegate(conn, tenant_id, approver, wanted, now), exposure_usd_minor=r["exposure_usd_minor"], decided=decided,
        presented_hash=presented_hash, request_hash=r["snapshot_hash"], review_required=r["review_required"], reviewed=r["reviewed_at"] is not None,
    )  # fmt: skip
    if decision == "REJECT" and not (reason or "").strip():
        raise CommitmentError("REASON_REQUIRED", "say why it is rejected")
    await conn.execute(
        text("INSERT INTO commercial.commitment_approval (request_id, tenant_id, principal_id, authority, decision, snapshot_hash, reason, step_up_at, decided_at) VALUES (:r, :t, :p, :a, :d, :h, :why, :s, :n)"),
        {"r": uuid.UUID(request_id), "t": uuid.UUID(tenant_id), "p": uuid.UUID(approver), "a": authority.value, "d": decision, "h": presented_hash, "why": reason, "s": step_up_at, "n": now},
    )  # fmt: skip
    if decision == "REJECT":
        await conn.execute(
            text(
                "UPDATE commercial.commitment_request SET state = 'REJECTED' WHERE request_id = :r"
            ),
            {"r": uuid.UUID(request_id)},
        )
    else:
        decided[approver] = authority.value
        if rules.is_complete(tier, decided):
            await conn.execute(
                text(
                    "UPDATE commercial.commitment_request SET state = 'APPROVED', approved_at = :n, approval_expires_at = :x WHERE request_id = :r"
                ),
                {
                    "r": uuid.UUID(request_id),
                    "n": now,
                    "x": now + timedelta(days=finance_policy().commitment.valid_days),
                },
            )
    await log_change(conn, kind="COMMITMENT_DECIDED", ref=request_id, tenant_id=tenant_id, actor=approver, reason=decision,
                     detail={"authority": authority.value, "snapshot_hash": presented_hash})  # fmt: skip
    return _view(await _load(conn, request_id), await _approvals(conn, request_id))


async def cancel(
    conn: AsyncConnection, *, request_id: str, by: str, now: datetime | None = None
) -> dict[str, Any]:
    r = await _lapse(conn, await _load(conn, request_id, lock=True), now or datetime.now(UTC))
    if str(r["requested_by"]) != by:
        raise CommitmentError("NOT_THE_REQUESTER", "only the person who asked cancels a request")
    if r["state"] not in ("PENDING_APPROVAL", "APPROVED"):
        raise CommitmentError(
            "NOT_CANCELABLE", f"a request that is {r['state']} cannot be canceled"
        )
    await conn.execute(
        text("UPDATE commercial.commitment_request SET state = 'CANCELED' WHERE request_id = :r"),
        {"r": uuid.UUID(request_id)},
    )
    await log_change(
        conn, kind="COMMITMENT_CANCELED", ref=request_id, tenant_id=str(r["tenant_id"]), actor=by
    )
    return _view(await _load(conn, request_id), await _approvals(conn, request_id))


async def mark_reviewed(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    request_id: str,
    reviewer: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Zoiko contract, legal and finance review of nonstandard terms: a Controller or a CFO marks it, once. Run under the customer's tenant scope."""
    now = now or datetime.now(UTC)
    if not (
        await finance.holds(conn, reviewer, "CONTROLLER", now)
        or await finance.holds(conn, reviewer, "CFO", now)
    ):
        raise CommitmentError(
            "NOT_A_REVIEWER", "only an appointed Controller or CFO marks nonstandard terms reviewed"
        )
    r = await _load(conn, request_id, lock=True)
    if not r["review_required"]:
        raise CommitmentError("NO_REVIEW_NEEDED", "these are standard terms")
    if r["reviewed_at"] is not None:
        raise CommitmentError("ALREADY_REVIEWED", "already marked reviewed")
    await conn.execute(
        text(
            "UPDATE commercial.commitment_request SET reviewed_by = :b, reviewed_at = :n WHERE request_id = :r"
        ),
        {"r": uuid.UUID(request_id), "b": uuid.UUID(reviewer), "n": now},
    )
    await log_change(
        conn, kind="COMMITMENT_REVIEWED", ref=request_id, tenant_id=tenant_id, actor=reviewer
    )
    return _view(await _load(conn, request_id), await _approvals(conn, request_id))


# ---------------------------------------------------------------------------------------------------------- the activation check


async def activation_problem(
    conn: AsyncConnection, *, order_id: str, tenant_id: str, now: datetime
) -> str | None:
    """Why this order has no valid customer approval right now, or None. Re-reads every fact: the order's quote and payer, each signer's authority."""
    async with in_tenant(conn, tenant_id):
        r = (
            (
                await conn.execute(
                    text(
                        "SELECT * FROM commercial.commitment_request WHERE order_id = :o ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"o": uuid.UUID(order_id)},
                )
            )
            .mappings()
            .first()
        )
        if r is None:
            return "NO_COMMITMENT_REQUEST"
        if r["state"] != "APPROVED":
            return f"COMMITMENT_{r['state']}"
        if r["approval_expires_at"] is None or r["approval_expires_at"] <= now:
            return "APPROVAL_EXPIRED"
        o = (
            await conn.execute(
                text("SELECT quote_id, account_id FROM commercial.sales_order WHERE order_id = :o"),
                {"o": uuid.UUID(order_id)},
            )
        ).first()
        q = (
            (
                await conn.execute(
                    text("SELECT digest FROM commercial.quote WHERE quote_id = :q"),
                    {"q": o.quote_id},
                )
            ).first()
            if o
            else None
        )
        if (
            o is None
            or q is None
            or q.digest != r["quote_digest"]
            or o.account_id != r["account_id"]
        ):
            return "ORDER_CHANGED"
        if r["policy_hash"] != finance_policy().content_hash:
            return "POLICY_CHANGED"
        for a in await _approvals(conn, str(r["request_id"])):
            if a["decision"] != "APPROVE":
                continue
            who = str(a["principal_id"])
            if a["authority"] == Authority.OWNER.value:
                ok = await _is_owner(conn, tenant_id, who)
            else:
                d = await _live_delegate(conn, tenant_id, who, a["authority"], now)
                ok = (
                    d is not None
                    and d.kind == a["authority"]
                    and d.cap_usd_minor >= r["exposure_usd_minor"]
                )
            if not ok:
                return "AUTHORITY_LOST"
    return None
