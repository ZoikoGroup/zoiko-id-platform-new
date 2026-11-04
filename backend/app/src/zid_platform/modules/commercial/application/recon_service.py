"""Reconciliation and period close (ZID-UX-COM-009 F04; slice L-14f, CR-058).

This is the controlled finance evidence queue, not the statutory general ledger. The platform's money events (a payment captured, a refund the processor confirmed, money a dispute
withdrew) are matched to the legs a Controller imports: the processor's payout line, the bank line for that payout, the ledger journal. A missing leg is never filled in from another,
so provider receipt, payout, bank settlement and the ledger stay separate facts. A period closes only with nothing blocking it and with two different people's signatures on the exact
digest of what was reconciled; once closed it is locked in the database, and reopening it takes two approvals from people other than the one who asked."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.application import finance_service as finance
from zid_platform.modules.commercial.application.change_log import log_change
from zid_platform.modules.commercial.domain import finance as fin
from zid_platform.modules.commercial.domain import payment_ops as ops
from zid_platform.modules.commercial.domain.payment_ops import (
    Item,
    Leg,
    MatchState,
    OpsError,
)
from zid_platform.modules.commercial.infrastructure.finance_config import finance_policy

LEG_TYPES = ("PAYOUT_LINE", "BANK_LINE", "GL_JOURNAL")
_PERIOD = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _policy() -> fin.ReconPolicy:
    p = finance_policy().recon
    if p is None:
        raise OpsError(
            "RECON_POLICY_ABSENT", "the signed finance policy names no reconciliation roles"
        )
    return p


def _bounds(period: str) -> tuple[date, date]:
    if not _PERIOD.match(period):
        raise OpsError("PERIOD_INVALID", "a period is YYYY-MM")
    y, m = int(period[:4]), int(period[5:])
    return date(y, m, 1), date(y + (m == 12), (m % 12) + 1, 1)


async def _state(conn: AsyncConnection, issuer: str, period: str) -> str:
    row = (
        await conn.execute(
            text(
                "SELECT state FROM commercial.recon_period WHERE issuing_entity = :e AND period = :p"
            ),
            {"e": issuer, "p": period},
        )
    ).first()
    return str(row[0]) if row else "OPEN"


async def _ensure(conn: AsyncConnection, issuer: str, period: str) -> None:
    await conn.execute(
        text(
            "INSERT INTO commercial.recon_period (issuing_entity, period, state) VALUES (:e, :p, 'OPEN') ON CONFLICT DO NOTHING"
        ),
        {"e": issuer, "p": period},
    )


async def import_legs(
    conn: AsyncConnection,
    *,
    issuing_entity: str,
    legs: list[dict[str, Any]],
    by: str,
    now: datetime | None = None,
) -> dict[str, int]:
    """Import processor payout lines, bank lines and ledger journals (from the reports the Controller holds). Idempotent: a leg already imported is skipped. A leg dated in a closed
    period is refused by the database, so a closed period cannot change under a signature."""
    now = now or datetime.now(UTC)
    policy = _policy()
    if not any([await finance.holds(conn, by, r, now) for r in policy.close_maker_roles]):
        raise fin.FinanceError(
            "NOT_A_CONTROLLER", "only an appointed Controller imports reconciliation evidence"
        )
    if not legs or len(legs) > 5000:
        raise OpsError("LEGS_INVALID", "import between 1 and 5000 lines at a time")
    added = skipped = 0
    for raw in legs:
        kind, ref = str(raw.get("leg_type", "")), str(raw.get("ref", "")).strip()
        if kind not in LEG_TYPES or not ref:
            raise OpsError("LEG_INVALID", "a leg names its type and its reference")
        try:
            amount, day = int(raw["amount_minor"]), date.fromisoformat(str(raw["occurred_on"]))
        except (KeyError, ValueError, TypeError) as e:
            raise OpsError("LEG_INVALID", "a leg has whole minor units and a date") from e
        digest = hashlib.sha256(
            json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        try:
            async with conn.begin_nested():
                res = await conn.execute(
                    text(
                        "INSERT INTO commercial.recon_leg (leg_id, leg_type, issuing_entity, currency, amount_minor, ref, link_ref, occurred_on, source_hash, imported_by, imported_at) "
                        "VALUES (:i, :k, :e, :c, :a, :r, :l, :d, :h, :by, :n) ON CONFLICT (leg_type, issuing_entity, ref, link_ref) DO NOTHING"
                    ),
                    {
                        "i": uuid.uuid4(),
                        "k": kind,
                        "e": issuing_entity,
                        "c": str(raw.get("currency", "")).upper(),
                        "a": amount,
                        "r": ref[:120],
                        "l": str(raw.get("link_ref", ""))[:120],
                        "d": day,
                        "h": digest,
                        "by": uuid.UUID(by),
                        "n": now,
                    },
                )
        except DBAPIError as e:
            if "is closed" in str(e):
                raise OpsError(
                    "PERIOD_CLOSED",
                    f"{day:%Y-%m} is closed: reopen it with two approvals before adding evidence",
                ) from e
            raise
        added += res.rowcount
        skipped += 1 - res.rowcount
    await log_change(
        conn,
        kind="RECON_LEGS_IMPORTED",
        ref=issuing_entity,
        tenant_id=None,
        actor=by,
        reason=None,
        detail={"added": added, "skipped": skipped},
    )
    return {"added": added, "skipped": skipped}


async def board(conn: AsyncConnection, issuing_entity: str, period: str) -> dict[str, Any]:
    """Every money event of the period with where each leg stands, the exceptions that raises, what blocks a close, and the digest a close would sign."""
    start, end = _bounds(period)
    policy = _policy()
    items = [
        Item(
            r["kind"],
            str(r["item_id"]),
            r["ref"],
            int(r["amount_minor"]),
            r["currency"],
            r["issuing_entity"],
            r["occurred_on"],
            r["journal_ref"],
        )
        for r in (
            await conn.execute(
                text(
                    "SELECT * FROM commercial.recon_item WHERE issuing_entity = :e AND occurred_on >= :s AND occurred_on < :n ORDER BY kind, item_id"
                ),
                {"e": issuing_entity, "s": start, "n": end},
            )
        )
        .mappings()
        .all()
    ]
    legs = [
        Leg(r["leg_type"], r["ref"], r["link_ref"], int(r["amount_minor"]), r["currency"])
        for r in (
            await conn.execute(
                text("SELECT * FROM commercial.recon_leg WHERE issuing_entity = :e"),
                {"e": issuing_entity},
            )
        )
        .mappings()
        .all()
    ]
    rows = [ops.match_item(i, legs) for i in items]
    resolved = frozenset(
        (r["source_kind"], str(r["source_id"]))
        for r in (
            await conn.execute(
                text(
                    "SELECT source_kind, source_id FROM commercial.recon_exception WHERE issuing_entity = :e AND period = :p AND resolution IS NOT NULL"
                ),
                {"e": issuing_entity, "p": period},
            )
        )
        .mappings()
        .all()
    )
    open_refunds = int(
        (
            await conn.execute(
                text(
                    "SELECT count(*) FROM commercial.refund_case WHERE issuing_entity = :e AND state IN ('EXECUTION_PENDING', 'PROCESSOR_PENDING', 'PARTIAL', 'UNKNOWN') AND created_at >= :s AND created_at < :n"
                ),
                {"e": issuing_entity, "s": start, "n": end},
            )
        ).scalar_one()
    )
    open_disputes = int(
        (
            await conn.execute(
                text(
                    "SELECT count(*) FROM commercial.dispute_case WHERE issuing_entity = :e AND state <> ALL(:f) AND created_at >= :s AND created_at < :n"
                ),
                {
                    "e": issuing_entity,
                    "f": [s.value for s in ops.DISPUTE_FINAL],
                    "s": start,
                    "n": end,
                },
            )
        ).scalar_one()
    )
    blockers = ops.close_blockers(
        rows,
        materiality_minor=policy.materiality_minor,
        open_refunds=open_refunds,
        open_disputes=open_disputes,
        resolved=resolved,
    )
    totals: dict[str, dict[str, int]] = {}
    for m in rows:
        t = totals.setdefault(m.item.currency, {"payments": 0, "refunds": 0, "disputes": 0})
        t[{"PAYMENT": "payments", "REFUND": "refunds", "DISPUTE": "disputes"}[m.item.kind]] += (
            m.item.amount_minor
        )
    return {
        "issuing_entity": issuing_entity,
        "period": period,
        "state": await _state(conn, issuing_entity, period),
        "rows": [
            {
                "kind": m.item.kind,
                "item_id": m.item.item_id,
                "ref": m.item.ref,
                "amount_minor": m.item.amount_minor,
                "currency": m.item.currency,
                "occurred_on": m.item.occurred_on.isoformat(),
                "state": m.state.value,
                "reason": m.reason,
                "difference_minor": m.difference_minor,
                "payout_ref": m.payout_ref,
                "legs": list(m.legs),
                "resolved": (m.item.kind, m.item.item_id) in resolved,
            }
            for m in rows
        ],
        "summary": {s.value: sum(1 for m in rows if m.state is s) for s in MatchState}
        | {"items": len(rows)},
        "settlement_note": "Provider receipt, payout, bank settlement and the ledger are separate facts. None of these totals is revenue.",
        "totals_minor": totals,
        "blockers": blockers,
        "ready": not blockers,
        "snapshot_hash": ops.board_hash(rows),
    }


async def raise_exceptions(
    conn: AsyncConnection, issuing_entity: str, period: str, *, now: datetime | None = None
) -> int:
    """Open an exception for every item that is not matched. An exception never disappears from the queue until a person resolves it with a reason."""
    now = now or datetime.now(UTC)
    b = await board(conn, issuing_entity, period)
    await _ensure(conn, issuing_entity, period)
    made = 0
    for r in b["rows"]:
        if r["state"] == MatchState.MATCHED.value:
            continue
        res = await conn.execute(
            text(
                "INSERT INTO commercial.recon_exception (exception_id, issuing_entity, period, source_kind, source_id, reason, created_at) VALUES (:i, :e, :p, :k, :s, :why, :n) "
                "ON CONFLICT (source_kind, source_id, period) DO NOTHING"
            ),
            {
                "i": uuid.uuid4(),
                "e": issuing_entity,
                "p": period,
                "k": r["kind"],
                "s": uuid.UUID(r["item_id"]),
                "why": (r["reason"] or "")[:200],
                "n": now,
            },
        )
        made += res.rowcount
    return made


async def resolve_exception(
    conn: AsyncConnection,
    exception_id: str,
    *,
    resolution: str,
    by: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """A Controller records how an exception was explained (a linked correction or reversal, an evidence request answered). Not a way to mark anything paid."""
    now = now or datetime.now(UTC)
    policy = _policy()
    if not any([await finance.holds(conn, by, r, now) for r in policy.close_maker_roles]):
        raise fin.FinanceError(
            "NOT_A_CONTROLLER", "only an appointed Controller resolves a reconciliation exception"
        )
    if not resolution.strip():
        raise OpsError(
            "RESOLUTION_REQUIRED",
            "say how it was explained, and link the correction or the evidence",
        )
    row = (
        (
            await conn.execute(
                text("SELECT * FROM commercial.recon_exception WHERE exception_id = :e FOR UPDATE"),
                {"e": uuid.UUID(exception_id)},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise OpsError("EXCEPTION_NOT_FOUND", "no such exception")
    try:
        await conn.execute(
            text(
                "UPDATE commercial.recon_exception SET resolution = :r, resolved_by = :b, resolved_at = :n WHERE exception_id = :e"
            ),
            {"e": row["exception_id"], "r": resolution.strip()[:500], "b": uuid.UUID(by), "n": now},
        )
    except DBAPIError as e:
        if "is closed" in str(e):
            raise OpsError("PERIOD_CLOSED", "the period is closed") from e
        raise
    await log_change(
        conn,
        kind="RECON_EXCEPTION_RESOLVED",
        ref=exception_id,
        tenant_id=None,
        actor=by,
        reason=None,
    )
    return {"exception_id": exception_id, "resolved": True}


async def exceptions(
    conn: AsyncConnection, issuing_entity: str, period: str
) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT * FROM commercial.recon_exception WHERE issuing_entity = :e AND period = :p ORDER BY created_at"
                ),
                {"e": issuing_entity, "p": period},
            )
        )
        .mappings()
        .all()
    )
    return [
        {
            "exception_id": str(r["exception_id"]),
            "source_kind": r["source_kind"],
            "source_id": str(r["source_id"]),
            "reason": r["reason"],
            "owner": str(r["owner"]) if r["owner"] else None,
            "resolution": r["resolution"],
            "resolved_at": r["resolved_at"].isoformat() if r["resolved_at"] else None,
        }
        for r in rows
    ]


async def prepare_close(
    conn: AsyncConnection, issuing_entity: str, period: str, *, by: str, now: datetime | None = None
) -> dict[str, Any]:
    """Ask whether the period can close. With blockers it stays open and says why; with none it is READY_FOR_CLOSE, bound to the digest of what was reconciled."""
    now = now or datetime.now(UTC)
    policy = _policy()
    if not any([await finance.holds(conn, by, r, now) for r in policy.close_maker_roles]):
        raise fin.FinanceError("NOT_A_CONTROLLER", "only an appointed Controller prepares a close")
    state = await _state(conn, issuing_entity, period)
    if state in ("CLOSED", "REOPEN_REQUESTED"):
        raise OpsError("PERIOD_NOT_OPEN", f"the period is {state}")
    await raise_exceptions(conn, issuing_entity, period, now=now)
    b = await board(conn, issuing_entity, period)
    await _ensure(conn, issuing_entity, period)
    if b["blockers"]:
        await conn.execute(
            text(
                "UPDATE commercial.recon_period SET state = 'OPEN', close_hash = NULL, updated_at = :n WHERE issuing_entity = :e AND period = :p"
            ),
            {"e": issuing_entity, "p": period, "n": now},
        )
        return {**b, "state": "OPEN"}
    await conn.execute(
        text(
            "UPDATE commercial.recon_period SET state = 'READY_FOR_CLOSE', close_hash = :h, updated_at = :n WHERE issuing_entity = :e AND period = :p"
        ),
        {"e": issuing_entity, "p": period, "h": b["snapshot_hash"], "n": now},
    )
    return {**b, "state": "READY_FOR_CLOSE"}


async def sign_close(
    conn: AsyncConnection,
    issuing_entity: str,
    period: str,
    *,
    role: str,
    signer: str,
    presented_hash: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One signature on the close: the maker's, then the checker's, by two different people, each on the digest they read. What was reconciled is read again at the
    last signature; if it moved, the period goes back to open and the signatures stay with the old digest."""
    now = now or datetime.now(UTC)
    policy = _policy()
    state = await _state(conn, issuing_entity, period)
    if state != "READY_FOR_CLOSE":
        raise OpsError("PERIOD_NOT_READY", f"the period is {state}, not ready for close")
    allowed = (*policy.close_maker_roles, *policy.close_checker_roles)
    if role not in allowed or not await finance.holds(conn, signer, role, now):
        raise fin.FinanceError("NOT_A_SIGNATORY", f"the signer does not hold {role} today")
    current = await board(conn, issuing_entity, period)
    given = {
        r["role"]: str(r["principal_id"])
        for r in (
            await conn.execute(
                text(
                    "SELECT role, principal_id FROM commercial.recon_close_approval WHERE issuing_entity = :e AND period = :p AND kind = 'CLOSE' AND snapshot_hash = :h"
                ),
                {"e": issuing_entity, "p": period, "h": presented_hash},
            )
        )
        .mappings()
        .all()
    }
    ops.check_close_signature(
        role=role,
        signer=signer,
        allowed=allowed,
        already=given,
        presented=presented_hash,
        current=current["snapshot_hash"],
        maker_roles=policy.close_maker_roles,
    )
    await conn.execute(
        text(
            "INSERT INTO commercial.recon_close_approval (approval_id, issuing_entity, period, kind, role, principal_id, snapshot_hash, decided_at) VALUES (:i, :e, :p, 'CLOSE', :r, :s, :h, :n)"
        ),
        {
            "i": uuid.uuid4(),
            "e": issuing_entity,
            "p": period,
            "r": role,
            "s": uuid.UUID(signer),
            "h": presented_hash,
            "n": now,
        },
    )
    signed = {**given, role: signer}
    maker = any(r in signed for r in policy.close_maker_roles)
    checker = any(r in signed for r in policy.close_checker_roles)
    if not (maker and checker):
        return {**current, "state": "READY_FOR_CLOSE", "signed_roles": sorted(signed)}
    receipt = {
        "issuing_entity": issuing_entity,
        "period": period,
        "snapshot_hash": presented_hash,
        "signers": signed,
        "closed_at": now.isoformat(),
        "policy_version": finance_policy().version,
        "totals_minor": current["totals_minor"],
        "items": current["summary"]["items"],
    }
    checksum = hashlib.sha256(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    receipt["checksum"] = checksum
    await conn.execute(
        text(
            "UPDATE commercial.recon_period SET state = 'CLOSED', close_hash = :h, receipt = CAST(:r AS jsonb), closed_at = :n, updated_at = :n WHERE issuing_entity = :e AND period = :p"
        ),
        {
            "e": issuing_entity,
            "p": period,
            "h": presented_hash,
            "r": json.dumps(receipt, sort_keys=True),
            "n": now,
        },
    )
    await log_change(
        conn,
        kind="RECON_PERIOD_CLOSED",
        ref=f"{issuing_entity}:{period}",
        tenant_id=None,
        actor=signer,
        reason=None,
        detail={"checksum": checksum},
    )
    return {**current, "state": "CLOSED", "receipt": receipt}


async def request_reopen(
    conn: AsyncConnection,
    issuing_entity: str,
    period: str,
    *,
    reason: str,
    impact: str,
    by: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    policy = _policy()
    if await _state(conn, issuing_entity, period) != "CLOSED":
        raise OpsError("PERIOD_NOT_CLOSED", "only a closed period is reopened")
    if not any(
        [
            await finance.holds(conn, by, r, now)
            for r in (*policy.close_maker_roles, *policy.reopen_roles)
        ]
    ):
        raise fin.FinanceError(
            "NOT_A_CONTROLLER",
            "only an appointed Controller or a reopening role asks to reopen a period",
        )
    if not reason.strip() or not impact.strip():
        raise OpsError("REASON_REQUIRED", "a reopen names its reason and its impact")
    await conn.execute(
        text(
            "UPDATE commercial.recon_period SET state = 'REOPEN_REQUESTED', reopen_reason = :r, reopen_impact = :i, reopen_requested_by = :b, updated_at = :n WHERE issuing_entity = :e AND period = :p"
        ),
        {
            "e": issuing_entity,
            "p": period,
            "r": reason.strip()[:500],
            "i": impact.strip()[:500],
            "b": uuid.UUID(by),
            "n": now,
        },
    )
    await log_change(
        conn,
        kind="RECON_REOPEN_REQUESTED",
        ref=f"{issuing_entity}:{period}",
        tenant_id=None,
        actor=by,
        reason=reason.strip()[:60],
    )
    return {"issuing_entity": issuing_entity, "period": period, "state": "REOPEN_REQUESTED"}


async def approve_reopen(
    conn: AsyncConnection,
    issuing_entity: str,
    period: str,
    *,
    role: str,
    approver: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Two different people from the reopening roles, neither the one who asked, approve. The period then opens again; its old receipt stays on record."""
    now = now or datetime.now(UTC)
    policy = _policy()
    row = (
        (
            await conn.execute(
                text(
                    "SELECT * FROM commercial.recon_period WHERE issuing_entity = :e AND period = :p FOR UPDATE"
                ),
                {"e": issuing_entity, "p": period},
            )
        )
        .mappings()
        .first()
    )
    if row is None or row["state"] != "REOPEN_REQUESTED":
        raise OpsError("NO_REOPEN_REQUESTED", "no reopen is waiting for approval")
    if role not in policy.reopen_roles or not await finance.holds(conn, approver, role, now):
        raise fin.FinanceError("NOT_A_SIGNATORY", f"the approver does not hold {role} today")
    if str(row["reopen_requested_by"]) == approver:
        raise fin.FinanceError("SELF_APPROVAL", "the person who asked to reopen never approves it")
    given = {
        r["role"]: str(r["principal_id"])
        for r in (
            await conn.execute(
                text(
                    "SELECT role, principal_id FROM commercial.recon_close_approval WHERE issuing_entity = :e AND period = :p AND kind = 'REOPEN' AND snapshot_hash = :h"
                ),
                {"e": issuing_entity, "p": period, "h": row["close_hash"]},
            )
        )
        .mappings()
        .all()
    }
    if role in given or approver in given.values():
        raise fin.FinanceError(
            "ONE_PERSON_TWO_SLOTS", "each role signs once and one person fills one role"
        )
    await conn.execute(
        text(
            "INSERT INTO commercial.recon_close_approval (approval_id, issuing_entity, period, kind, role, principal_id, snapshot_hash, decided_at) VALUES (:i, :e, :p, 'REOPEN', :r, :s, :h, :n)"
        ),
        {
            "i": uuid.uuid4(),
            "e": issuing_entity,
            "p": period,
            "r": role,
            "s": uuid.UUID(approver),
            "h": row["close_hash"],
            "n": now,
        },
    )
    signed = {**given, role: approver}
    if not all(r in signed for r in policy.reopen_roles):
        return {
            "issuing_entity": issuing_entity,
            "period": period,
            "state": "REOPEN_REQUESTED",
            "signed_roles": sorted(signed),
        }
    await conn.execute(
        text(
            "UPDATE commercial.recon_period SET state = 'OPEN', close_hash = NULL, updated_at = :n WHERE issuing_entity = :e AND period = :p"
        ),
        {"e": issuing_entity, "p": period, "n": now},
    )
    await log_change(
        conn,
        kind="RECON_PERIOD_REOPENED",
        ref=f"{issuing_entity}:{period}",
        tenant_id=None,
        actor=approver,
        reason=None,
    )
    return {"issuing_entity": issuing_entity, "period": period, "state": "OPEN"}


async def evidence_pack(conn: AsyncConnection, issuing_entity: str, period: str) -> dict[str, Any]:
    """A read-only evidence pack for a closed period: its receipt, the reconciled rows as they were signed, and a checksum over the lot."""
    row = (
        (
            await conn.execute(
                text(
                    "SELECT state, receipt FROM commercial.recon_period WHERE issuing_entity = :e AND period = :p"
                ),
                {"e": issuing_entity, "p": period},
            )
        )
        .mappings()
        .first()
    )
    if row is None or row["state"] != "CLOSED" or not row["receipt"]:
        raise OpsError("PERIOD_NOT_CLOSED", "an evidence pack is made for a closed period")
    b = await board(conn, issuing_entity, period)
    pack = {
        "receipt": row["receipt"],
        "rows": b["rows"],
        "policy_version": finance_policy().version,
        "unchanged_since_close": b["snapshot_hash"] == row["receipt"]["snapshot_hash"],
    }
    pack["checksum"] = hashlib.sha256(
        json.dumps(pack, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return pack
