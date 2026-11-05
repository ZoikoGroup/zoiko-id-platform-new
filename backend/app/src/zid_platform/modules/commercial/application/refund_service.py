"""Refunds: requested and approved in Zoiko iD, executed through the processor by a worker (ZID-UX-COM-009 F03; slice L-14f, CR-058).

A refund case pins its source payment, its amount and the digest every approval signs. Approving it, the processor accepting it, the processor saying the money moved and the
bank and ledger agreeing are different facts, so the state says which one is known. Nothing here sends money inside the person's request: `execute` queues the case, and the worker
sends it once under the case's own idempotency key. A processor that cannot be reached leaves the case UNKNOWN, and only an answer to "what do you hold for this case" lets it be
sent again. A refund is limited to what the customer overpaid, so a refund and a credit note for the same money are never both granted."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import in_tenant

from zid_platform.modules.commercial.application import finance_service as finance
from zid_platform.modules.commercial.application import invoicing_service as invoicing
from zid_platform.modules.commercial.application.adapters import (
    RefundPort,
    RefundResult,
    to_minor_units,
)
from zid_platform.modules.commercial.application.change_log import log_change
from zid_platform.modules.commercial.domain import finance as fin
from zid_platform.modules.commercial.domain import payment_ops as ops
from zid_platform.modules.commercial.domain.payment_ops import OpsError, RefundState
from zid_platform.modules.commercial.infrastructure.finance_config import finance_policy

COUNTED = [s.value for s in ops.REFUND_COUNTS]


def _policy() -> fin.Instrument:
    inst = finance_policy().refund
    if inst is None:
        raise OpsError(
            "REFUND_POLICY_ABSENT",
            "the signed finance policy has no refund tiers, so no refund can be requested or approved",
        )
    return inst


async def _load(conn: AsyncConnection, refund_id: str, *, lock: bool = False) -> Any:
    row = (
        (
            await conn.execute(
                text(
                    "SELECT * FROM commercial.refund_case WHERE refund_id = :r"
                    + (" FOR UPDATE" if lock else "")
                ),
                {"r": uuid.UUID(refund_id)},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise OpsError("REFUND_NOT_FOUND", "no such refund case")
    return row


async def _approvals(conn: AsyncConnection, refund_id: str) -> list[Any]:
    return list(
        (
            await conn.execute(
                text(
                    "SELECT role, principal_id, decision, decided_at FROM commercial.refund_approval WHERE refund_id = :r ORDER BY decided_at"
                ),
                {"r": uuid.UUID(refund_id)},
            )
        )
        .mappings()
        .all()
    )


async def facts(
    conn: AsyncConnection, tenant_id: str, payment_attempt_id: str, *, exclude: str | None = None
) -> dict[str, Any]:
    """The facts a refund depends on, read now: the payment, its invoice, the payer, what was credited and what was refunded. Read again at execution."""
    async with in_tenant(conn, tenant_id):
        pay = (
            (
                await conn.execute(
                    text(
                        "SELECT attempt_id, invoice_id, amount, currency, status, provider_ref FROM commercial.payment_attempt WHERE attempt_id = :a AND tenant_id = :t"
                    ),
                    {"a": uuid.UUID(payment_attempt_id), "t": uuid.UUID(tenant_id)},
                )
            )
            .mappings()
            .first()
        )
        if pay is None:
            raise OpsError("PAYMENT_NOT_FOUND", "no such payment for this organization")
        inv = (
            (
                await conn.execute(
                    text(
                        "SELECT invoice_id, issuing_entity, gross, currency, status FROM commercial.invoice WHERE invoice_id = :i"
                    ),
                    {"i": pay["invoice_id"]},
                )
            )
            .mappings()
            .first()
        )
        credited = (
            await conn.execute(
                text(
                    "SELECT coalesce(sum(amount), 0) FROM commercial.credit_note WHERE invoice_id = :i"
                ),
                {"i": pay["invoice_id"]},
            )
        ).scalar_one()
        paid = await invoicing.amount_paid(conn, str(pay["invoice_id"]))
        account = await invoicing.account_of_tenant(conn, tenant_id)
    assert inv is not None
    cur = str(pay["currency"])
    refunded_invoice, refunded_payment = (
        await conn.execute(
            text(
                "SELECT coalesce(sum(amount_minor) FILTER (WHERE invoice_id = :i), 0), coalesce(sum(amount_minor) FILTER (WHERE payment_attempt_id = :a), 0) "
                "FROM commercial.refund_case WHERE (invoice_id = :i OR payment_attempt_id = :a) AND state = ANY(:s) AND (CAST(:x AS uuid) IS NULL OR refund_id <> CAST(:x AS uuid))"
            ),
            {"i": pay["invoice_id"], "a": pay["attempt_id"], "s": COUNTED, "x": exclude},
        )
    ).one()
    disputed = bool(
        pay["provider_ref"]
        and (
            await conn.execute(
                text(
                    "SELECT 1 FROM commercial.dispute_case WHERE payment_intent_ref = :p AND state <> ALL(:f) LIMIT 1"
                ),
                {"p": pay["provider_ref"], "f": [s.value for s in ops.DISPUTE_FINAL]},
            )
        ).first()
    )
    return {
        "payment_attempt_id": str(pay["attempt_id"]),
        "invoice_id": str(pay["invoice_id"]),
        "provider_ref": pay["provider_ref"],
        "captured": pay["status"] == "CAPTURED",
        "issuing_entity": inv["issuing_entity"],
        "invoice_status": inv["status"],
        "currency": cur,
        "account_id": account["account_id"] if account else None,
        "payment_minor": to_minor_units(Decimal(pay["amount"]), cur),
        "paid_minor": to_minor_units(Decimal(paid), cur),
        "gross_minor": to_minor_units(Decimal(inv["gross"]), cur),
        "credited_minor": to_minor_units(Decimal(credited), cur),
        "refunded_invoice_minor": int(refunded_invoice),
        "refunded_payment_minor": int(refunded_payment),
        "disputed": disputed,
    }


def _refundable(f: dict[str, Any]) -> int:
    by_invoice = ops.refundable_minor(
        paid_minor=f["paid_minor"],
        gross_minor=f["gross_minor"],
        credited_minor=f["credited_minor"],
        refunded_minor=f["refunded_invoice_minor"],
    )
    return int(max(min(by_invoice, f["payment_minor"] - f["refunded_payment_minor"]), 0))


async def open_refund(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    payment_attempt_id: str,
    amount: Decimal,
    category: str,
    reason: str,
    requested_by: str,
    evidence_ref: str | None = None,
    credit_note_id: str | None = None,
    environment: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Ask for a refund of one captured payment. The tier, and so who must sign, is the policy's and is worked out here from the exposure."""
    now = now or datetime.now(UTC)
    policy = finance_policy()
    instrument = _policy()
    await finance.require_effective(conn, environment)
    if not any([await finance.holds(conn, requested_by, r, now) for r in policy.request_roles]):
        raise fin.FinanceError(
            "NOT_A_REQUESTER",
            "only an appointed Finance Operations Lead or RevOps opens a finance request",
        )
    if not reason.strip():
        raise fin.FinanceError("REASON_REQUIRED", "say why")
    f = await facts(conn, tenant_id, payment_attempt_id)
    if f["invoice_status"] != "ISSUED":
        raise OpsError("INVOICE_NOT_REFUNDABLE", "only an issued invoice's payment is refunded")
    minor = to_minor_units(amount, f["currency"])
    ops.check_refund_request(
        amount_minor=minor,
        refundable=_refundable(f),
        disputed=f["disputed"],
        category=category,
        evidence_ref=evidence_ref,
        captured=f["captured"],
    )
    if credit_note_id is not None:
        async with in_tenant(conn, tenant_id):
            linked = (
                await conn.execute(
                    text(
                        "SELECT 1 FROM commercial.credit_note WHERE credit_note_id = :c AND invoice_id = :i"
                    ),
                    {"c": uuid.UUID(credit_note_id), "i": uuid.UUID(f["invoice_id"])},
                )
            ).first()
        if linked is None:
            raise OpsError(
                "CREDIT_NOTE_NOT_FOR_THIS_PAYMENT",
                "the credit note is not against this payment's invoice",
            )
    usd = fin.usd_minor(minor, f["currency"], policy.fx_usd)
    window = now - timedelta(days=instrument.window_days)
    held = (
        await conn.execute(
            text(
                "SELECT coalesce(sum(usd_minor), 0) FROM commercial.refund_case WHERE tenant_id = :t AND state = ANY(:s) AND created_at > :w"
            ),
            {"t": uuid.UUID(tenant_id), "s": COUNTED, "w": window},
        )
    ).scalar_one()
    exposure = (
        int(held) + usd
    )  # refunds for one organization add up, so one cannot be split to stay under a limit
    tier = fin.tier_for(instrument, exposure)
    refund_id = str(uuid.uuid4())
    snapshot = {
        "kind": "REFUND",
        "tenant_id": tenant_id,
        "payment_attempt_id": payment_attempt_id,
        "invoice_id": f["invoice_id"],
        "provider_ref": f["provider_ref"],
        "issuing_entity": f["issuing_entity"],
        "account_id": f["account_id"],
        "currency": f["currency"],
        "amount_minor": minor,
        "category": category,
        "reason": reason.strip(),
        "evidence_ref": evidence_ref,
        "credit_note_id": credit_note_id,
        "paid_minor": f["paid_minor"],
        "gross_minor": f["gross_minor"],
        "credited_minor": f["credited_minor"],
        "policy_version": policy.version,
    }
    digest = fin.snapshot_hash(snapshot)
    await conn.execute(
        text(
            "INSERT INTO commercial.refund_case (refund_id, tenant_id, invoice_id, payment_attempt_id, issuing_entity, currency, amount_minor, usd_minor, exposure_usd_minor, category, "
            "reason, evidence_ref, credit_note_id, snapshot, snapshot_hash, policy_version, required_roles, state, requested_by, created_at, expires_at, idempotency_key) VALUES "
            "(:r, :t, :i, :p, :e, :c, :m, :u, :x, :cat, :why, :ev, :cn, CAST(:snap AS jsonb), :h, :v, :roles, 'REQUESTED', :by, :now, :exp, :idem)"
        ),
        {
            "r": uuid.UUID(refund_id),
            "t": uuid.UUID(tenant_id),
            "i": uuid.UUID(f["invoice_id"]),
            "p": uuid.UUID(payment_attempt_id),
            "e": f["issuing_entity"],
            "c": f["currency"],
            "m": minor,
            "u": usd,
            "x": exposure,
            "cat": category,
            "why": reason.strip(),
            "ev": evidence_ref,
            "cn": uuid.UUID(credit_note_id) if credit_note_id else None,
            "snap": json.dumps(snapshot, sort_keys=True),
            "h": digest,
            "v": policy.version,
            "roles": list(tier.roles),
            "by": uuid.UUID(requested_by),
            "now": now,
            "exp": now + timedelta(days=policy.request_valid_days),
            "idem": f"refund-{refund_id}",
        },
    )
    await log_change(
        conn,
        kind="REFUND_REQUESTED",
        ref=refund_id,
        tenant_id=tenant_id,
        actor=requested_by,
        reason=category,
        detail={"amount_minor": minor, "currency": f["currency"], "tier_roles": list(tier.roles)},
    )
    return await view(conn, refund_id)


async def _lapse(conn: AsyncConnection, row: Any, now: datetime) -> Any:
    state = RefundState(row["state"])
    late = (state is RefundState.REQUESTED and row["expires_at"] <= now) or (
        state is RefundState.APPROVED
        and row["approval_expires_at"] is not None
        and row["approval_expires_at"] <= now
    )
    if not late:
        return row
    await conn.execute(
        text(
            "UPDATE commercial.refund_case SET state = 'EXPIRED', updated_at = :n WHERE refund_id = :r"
        ),
        {"r": row["refund_id"], "n": now},
    )
    await log_change(
        conn,
        kind="REFUND_EXPIRED",
        ref=str(row["refund_id"]),
        tenant_id=str(row["tenant_id"]),
        actor=None,
        reason=state.value,
    )
    return await _load(conn, str(row["refund_id"]))


async def decide(
    conn: AsyncConnection,
    refund_id: str,
    *,
    role: str,
    approver: str,
    decision: str,
    presented_hash: str,
    reason: str | None,
    environment: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    policy = finance_policy()
    row = await _lapse(conn, await _load(conn, refund_id, lock=True), now)
    if RefundState(row["state"]) is not RefundState.REQUESTED:
        raise OpsError("NOT_PENDING", f"a refund that is {row['state']} is not decided")
    await finance.require_effective(conn, environment)
    if decision not in ("APPROVE", "REJECT"):
        raise fin.FinanceError("DECISION_INVALID", decision)
    if not await finance.holds(conn, approver, role, now):
        raise fin.FinanceError("NOT_A_SIGNATORY", f"the approver does not hold {role} today")
    decided = {
        r["role"]: str(r["principal_id"])
        for r in await _approvals(conn, refund_id)
        if r["decision"] == "APPROVE"
    }
    fin.check_decision(
        required=tuple(row["required_roles"]),
        role=role,
        approver=approver,
        requester=str(row["requested_by"]),
        decided=decided,
        presented_hash=presented_hash,
        request_hash=row["snapshot_hash"],
    )
    if decision == "REJECT" and not (reason or "").strip():
        raise fin.FinanceError("REASON_REQUIRED", "say why it is rejected")
    await conn.execute(
        text(
            "INSERT INTO commercial.refund_approval (refund_id, role, principal_id, decision, snapshot_hash, reason, decided_at) VALUES (:r, :ro, :p, :d, :h, :why, :n)"
        ),
        {
            "r": uuid.UUID(refund_id),
            "ro": role,
            "p": uuid.UUID(approver),
            "d": decision,
            "h": presented_hash,
            "why": reason,
            "n": now,
        },
    )
    if decision == "REJECT":
        await conn.execute(
            text(
                "UPDATE commercial.refund_case SET state = 'REJECTED', updated_at = :n WHERE refund_id = :r"
            ),
            {"r": uuid.UUID(refund_id), "n": now},
        )
    elif fin.is_complete(tuple(row["required_roles"]), {**decided, role: approver}):
        await conn.execute(
            text(
                "UPDATE commercial.refund_case SET state = 'APPROVED', approved_at = :n, approval_expires_at = :e, updated_at = :n WHERE refund_id = :r"
            ),
            {
                "r": uuid.UUID(refund_id),
                "n": now,
                "e": now + timedelta(days=policy.approval_valid_days),
            },
        )
    await log_change(
        conn,
        kind="REFUND_DECIDED",
        ref=refund_id,
        tenant_id=str(row["tenant_id"]),
        actor=approver,
        reason=decision,
        detail={"role": role},
    )
    return await view(conn, refund_id)


async def execute(
    conn: AsyncConnection,
    refund_id: str,
    *,
    executor: str,
    environment: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Queue an approved refund for the worker. Nothing is sent to the processor here. Everything the approval rested on is read again first: the payer, the payment,
    what is left to refund and whether the payment is in dispute. If any of it moved, the approval is void and a new request is needed."""
    now = now or datetime.now(UTC)
    policy = finance_policy()
    row = await _lapse(conn, await _load(conn, refund_id, lock=True), now)
    state = RefundState(row["state"])
    if state is not RefundState.APPROVED:
        raise OpsError("NOT_APPROVED", f"a refund that is {state.value} is not run")
    if row["approval_expires_at"] is None or row["approval_expires_at"] <= now:
        raise OpsError("APPROVAL_EXPIRED", "the approval lapsed before the refund was run")
    await finance.require_effective(conn, environment)
    if not any([await finance.holds(conn, executor, r, now) for r in policy.execute_roles]):
        raise fin.FinanceError(
            "NOT_AN_EXECUTOR", "only an appointed Finance Executor runs an approved request"
        )
    approvers = tuple(str(a["principal_id"]) for a in await _approvals(conn, refund_id))
    if executor == str(row["requested_by"]) or executor in approvers:
        raise fin.FinanceError(
            "EXECUTOR_NOT_INDEPENDENT", "whoever runs it neither asked for it nor signed it"
        )
    for role in row[
        "required_roles"
    ]:  # the signers' roles are read again: an ended appointment voids what it signed
        who = next(
            (
                str(a["principal_id"])
                for a in await _approvals(conn, refund_id)
                if a["role"] == role and a["decision"] == "APPROVE"
            ),
            None,
        )
        if who is None or not await finance.holds(conn, who, role, now):
            await _void(conn, row, "SIGNER_NO_LONGER_APPOINTED", now)
            raise fin.FinanceError(
                "SIGNER_NO_LONGER_APPOINTED",
                "a signer no longer holds the role they signed as: the approval is void",
            )
    snap = row["snapshot"]
    f = await facts(conn, str(row["tenant_id"]), str(row["payment_attempt_id"]), exclude=refund_id)
    for key, now_value in (
        ("issuing_entity", f["issuing_entity"]),
        ("account_id", f["account_id"]),
        ("currency", f["currency"]),
        ("provider_ref", f["provider_ref"]),
    ):
        if snap.get(key) != now_value:
            await _void(conn, row, f"{key.upper()}_CHANGED", now)
            raise OpsError(
                f"{key.upper()}_CHANGED",
                f"the {key.replace('_', ' ')} changed after approval: the approval is void",
            )
    if f["disputed"]:
        raise OpsError("REFUND_BLOCKED_BY_DISPUTE", "the payment is in dispute")
    if int(row["amount_minor"]) > _refundable(f):
        await _void(conn, row, "BALANCE_CHANGED", now)
        raise OpsError(
            "REFUND_EXCEEDS_OVERPAYMENT",
            "less is refundable now than was approved: the approval is void",
        )
    await conn.execute(
        text(
            "UPDATE commercial.refund_case SET state = 'EXECUTION_PENDING', executed_by = :x, executed_at = :n, updated_at = :n WHERE refund_id = :r"
        ),
        {"r": uuid.UUID(refund_id), "x": uuid.UUID(executor), "n": now},
    )
    await log_change(
        conn,
        kind="REFUND_QUEUED",
        ref=refund_id,
        tenant_id=str(row["tenant_id"]),
        actor=executor,
        reason=None,
        detail={},
    )
    return await view(conn, refund_id)


async def _void(conn: AsyncConnection, row: Any, code: str, now: datetime) -> None:
    await conn.execute(
        text(
            "UPDATE commercial.refund_case SET state = 'EXPIRED', failure_code = :c, updated_at = :n WHERE refund_id = :r"
        ),
        {"r": row["refund_id"], "c": code[:80], "n": now},
    )
    await log_change(
        conn,
        kind="REFUND_VOIDED",
        ref=str(row["refund_id"]),
        tenant_id=str(row["tenant_id"]),
        actor=None,
        reason=code,
    )


async def cancel(
    conn: AsyncConnection, refund_id: str, *, by: str, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    row = await _load(conn, refund_id, lock=True)
    if str(row["requested_by"]) != by:
        raise fin.FinanceError(
            "NOT_THE_REQUESTER", "only the person who asked withdraws the request"
        )
    if RefundState(row["state"]) not in (RefundState.REQUESTED, RefundState.APPROVED):
        raise OpsError("NOT_CANCELABLE", f"a refund that is {row['state']} cannot be withdrawn")
    await conn.execute(
        text(
            "UPDATE commercial.refund_case SET state = 'CANCELED', updated_at = :n WHERE refund_id = :r"
        ),
        {"r": uuid.UUID(refund_id), "n": now},
    )
    await log_change(
        conn,
        kind="REFUND_CANCELED",
        ref=refund_id,
        tenant_id=str(row["tenant_id"]),
        actor=by,
        reason=None,
    )
    return await view(conn, refund_id)


# -------------------------------------------------------------------------------------------------------------- the processor side


async def _apply_result(
    conn: AsyncConnection, row: Any, res: RefundResult, now: datetime
) -> RefundState:
    """Record what the processor said. A recognised, completed answer is the only success; an answer nobody recognises is UNKNOWN; no answer is UNKNOWN too."""
    current = RefundState(row["state"])
    code: str | None
    verified: datetime | None
    if res.outcome == "UNAVAILABLE":
        target, code, verified = RefundState.UNKNOWN, "PROVIDER_UNAVAILABLE", None
    elif res.outcome == "REFUSED":
        target, code, verified = RefundState.FAILED, res.failure_code or "PROVIDER_REFUSED", now
    else:
        target, code = ops.provider_refund_outcome(
            res.status, res.amount_minor, int(row["amount_minor"])
        )
        verified = now
    target = ops.refund_transition(current, target)
    await conn.execute(
        text(
            "UPDATE commercial.refund_case SET state = :s, provider_refund_ref = coalesce(:ref, provider_refund_ref), provider_state = coalesce(:ps, provider_state), "
            "provider_amount_minor = coalesce(:pa, provider_amount_minor), last_verified_at = coalesce(:v, last_verified_at), failure_code = :f, updated_at = :n WHERE refund_id = :r"
        ),
        {
            "r": row["refund_id"],
            "s": target.value,
            "ref": res.refund_ref,
            "ps": res.status,
            "pa": res.amount_minor,
            "v": verified,
            "f": code,
            "n": now,
        },
    )
    if target in (RefundState.SUCCEEDED, RefundState.PARTIAL) and res.refund_ref:
        await conn.execute(  # the processor's own word that money moved: an item for reconciliation, a negative amount
            text(
                "INSERT INTO commercial.recon_item (item_id, kind, tenant_id, issuing_entity, currency, amount_minor, ref, journal_ref, occurred_on) "
                "VALUES (:i, 'REFUND', :t, :e, :c, :a, :ref, '', :d) ON CONFLICT (kind, ref) DO UPDATE SET amount_minor = EXCLUDED.amount_minor"
            ),
            {
                "i": row["refund_id"],
                "t": row["tenant_id"],
                "e": row["issuing_entity"],
                "c": row["currency"],
                "a": -int(res.amount_minor or row["amount_minor"]),
                "ref": res.refund_ref,
                "d": now.date(),
            },
        )
    await log_change(
        conn,
        kind="REFUND_PROCESSOR_STATE",
        ref=str(row["refund_id"]),
        tenant_id=str(row["tenant_id"]),
        actor=None,
        reason=target.value,
        detail={"provider_state": res.status, "code": code},
    )
    return target


async def send_pending(
    conn: AsyncConnection, port: RefundPort, now: datetime | None = None, *, limit: int = 20
) -> dict[str, int]:
    """The outbox: send each queued refund once, under the case's own idempotency key, then record what the processor answered. A second pass never sends it again."""
    now = now or datetime.now(UTC)
    out = {"sent": 0, "unknown": 0, "failed": 0}
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT * FROM commercial.refund_case WHERE state = 'EXECUTION_PENDING' ORDER BY updated_at LIMIT :n FOR UPDATE SKIP LOCKED"
                ),
                {"n": limit},
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        f = await facts(
            conn,
            str(row["tenant_id"]),
            str(row["payment_attempt_id"]),
            exclude=str(row["refund_id"]),
        )
        await conn.execute(
            text("UPDATE commercial.refund_case SET attempts = attempts + 1 WHERE refund_id = :r"),
            {"r": row["refund_id"]},
        )
        res = await port.create(
            payment_intent=str(f["provider_ref"]),
            amount_minor=int(row["amount_minor"]),
            currency=row["currency"],
            idempotency_key=row["idempotency_key"],
            request_ref=str(row["refund_id"]),
        )
        state = await _apply_result(conn, row, res, now)
        out[
            "failed"
            if state is RefundState.FAILED
            else "unknown"
            if state is RefundState.UNKNOWN
            else "sent"
        ] += 1
    return out


async def verify_open(
    conn: AsyncConnection, port: RefundPort, now: datetime | None = None, *, limit: int = 50
) -> dict[str, int]:
    """Ask the processor what it holds for every refund whose outcome is not known. A case the processor holds nothing for is queued to be sent again, under the same
    key, and only then; an unreachable processor changes nothing."""
    now = now or datetime.now(UTC)
    out = {"verified": 0, "resend": 0, "unreachable": 0}
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT * FROM commercial.refund_case WHERE state IN ('PROCESSOR_PENDING', 'PARTIAL', 'UNKNOWN') ORDER BY updated_at LIMIT :n FOR UPDATE SKIP LOCKED"
                ),
                {"n": limit},
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        if row["provider_refund_ref"]:
            res: RefundResult | None = await port.retrieve(row["provider_refund_ref"])
        else:
            f = await facts(
                conn,
                str(row["tenant_id"]),
                str(row["payment_attempt_id"]),
                exclude=str(row["refund_id"]),
            )
            res = await port.find(
                payment_intent=str(f["provider_ref"]), request_ref=str(row["refund_id"])
            )
        if res is not None and res.outcome == "UNAVAILABLE":
            out["unreachable"] += 1
            continue
        if res is None:  # the processor answered and holds nothing for this case
            if ops.may_send_again(RefundState(row["state"]), provider_holds_none=True):
                await conn.execute(
                    text(
                        "UPDATE commercial.refund_case SET state = 'EXECUTION_PENDING', failure_code = NULL, updated_at = :n WHERE refund_id = :r"
                    ),
                    {"r": row["refund_id"], "n": now},
                )
                out["resend"] += 1
            continue
        await _apply_result(conn, row, res, now)
        out["verified"] += 1
    return out


async def note_provider_refund(
    conn: AsyncConnection,
    *,
    refund_ref: str | None,
    case_ref: str | None,
    payment_intent: str | None,
    now: datetime,
) -> str | None:
    """A verified processor event about a refund. If it is one of this platform's own cases it is linked (and the next verification pass reads the authoritative state);
    returns the case id, or None when nobody here asked for it (a refund made in the processor's dashboard is a person's to review)."""
    row = None
    if refund_ref:
        row = (
            await conn.execute(
                text("SELECT refund_id FROM commercial.refund_case WHERE provider_refund_ref = :r"),
                {"r": refund_ref},
            )
        ).first()
    if row is None and case_ref:
        try:
            row = (
                await conn.execute(
                    text("SELECT refund_id FROM commercial.refund_case WHERE refund_id = :r"),
                    {"r": uuid.UUID(case_ref)},
                )
            ).first()
        except ValueError:
            row = None
    if row is None and payment_intent:
        row = (
            await conn.execute(
                text(
                    "SELECT c.refund_id FROM commercial.refund_case c JOIN commercial.payment_attempt a ON a.attempt_id = c.payment_attempt_id "
                    "WHERE a.provider_ref = :p AND c.state IN ('EXECUTION_PENDING', 'PROCESSOR_PENDING', 'PARTIAL', 'UNKNOWN', 'SUCCEEDED') ORDER BY c.created_at DESC LIMIT 1"
                ),
                {"p": payment_intent},
            )
        ).first()
    if row is None:
        return None
    await conn.execute(
        text(
            "UPDATE commercial.refund_case SET provider_refund_ref = coalesce(provider_refund_ref, :ref), updated_at = :n WHERE refund_id = :r"
        ),
        {"r": row[0], "ref": refund_ref, "n": now},
    )
    return str(row[0])


# -------------------------------------------------------------------------------------------------------------------------- reading


async def view(conn: AsyncConnection, refund_id: str) -> dict[str, Any]:
    r = await _load(conn, refund_id)
    state = RefundState(r["state"])
    label, never = ops.REFUND_WORDS[state]
    f = await facts(conn, str(r["tenant_id"]), str(r["payment_attempt_id"]), exclude=refund_id)
    return {
        "refund_id": str(r["refund_id"]),
        "tenant_id": str(r["tenant_id"]),
        "invoice_id": str(r["invoice_id"]),
        "payment_attempt_id": str(r["payment_attempt_id"]),
        "issuing_entity": r["issuing_entity"],
        "currency": r["currency"],
        "amount_minor": int(r["amount_minor"]),
        "usd_minor": int(r["usd_minor"]),
        "exposure_usd_minor": int(r["exposure_usd_minor"]),
        "category": r["category"],
        "reason": r["reason"],
        "evidence_ref": r["evidence_ref"],
        "credit_note_id": str(r["credit_note_id"]) if r["credit_note_id"] else None,
        "snapshot_hash": r["snapshot_hash"],
        "policy_version": r["policy_version"],
        "required_roles": list(r["required_roles"]),
        "state": state.value,
        "label": label,
        "never_claim": never,
        "requested_by": str(r["requested_by"]),
        "created_at": r["created_at"].isoformat(),
        "expires_at": r["expires_at"].isoformat(),
        "approved_at": r["approved_at"].isoformat() if r["approved_at"] else None,
        "approval_expires_at": r["approval_expires_at"].isoformat()
        if r["approval_expires_at"]
        else None,
        "approvals": [
            {
                "role": a["role"],
                "principal_id": str(a["principal_id"]),
                "decision": a["decision"],
                "decided_at": a["decided_at"].isoformat(),
            }
            for a in await _approvals(conn, refund_id)
        ],
        "processor": {
            "refund_ref": r["provider_refund_ref"],
            "state": r["provider_state"],
            "amount_minor": r["provider_amount_minor"],
            "last_verified_at": r["last_verified_at"].isoformat()
            if r["last_verified_at"]
            else None,
            "failure_code": r["failure_code"],
            "attempts": int(r["attempts"]),
        },
        "overlap": {
            "open_dispute": f["disputed"],
            "credited_minor": f["credited_minor"],
            "refunded_other_minor": f["refunded_invoice_minor"],
            "refundable_minor": _refundable(f),
        },
    }


async def list_cases(
    conn: AsyncConnection, *, state: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT refund_id FROM commercial.refund_case WHERE (CAST(:s AS text) IS NULL OR state = CAST(:s AS text)) ORDER BY updated_at DESC LIMIT :n"
            ),
            {"s": state, "n": limit},
        )
    ).all()
    return [await view(conn, str(r[0])) for r in rows]
