"""Disputes: governed in Zoiko iD, handled at the processor (ZID-UX-COM-009 F03; slice L-14f, CR-058).

A verified processor event creates or updates one case per dispute, merchant and live mode, so a redelivery never makes a second. A specialist owns the case and its deadlines;
a Controller or the CFO decides whether to accept or contest it; a separately appointed processor operator records the hand-off to the processor's dashboard, and the platform then
asks the processor whether it really holds the submission. A "submitted" checkbox is not an outcome and neither is a manual verdict: the close of a dispute is the processor's.
This is the Payments dispute workflow for Zoiko's merchant account, never the Issuing cardholder one."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.application import finance_service as finance
from zid_platform.modules.commercial.application.adapters import RefundPort
from zid_platform.modules.commercial.application.change_log import log_change
from zid_platform.modules.commercial.domain import finance as fin
from zid_platform.modules.commercial.domain import payment_ops as ops
from zid_platform.modules.commercial.domain.payment_ops import DisputeState, OpsError
from zid_platform.modules.commercial.infrastructure.finance_config import finance_policy

EVENT_TYPES = frozenset(
    {
        "charge.dispute.created",
        "charge.dispute.updated",
        "charge.dispute.closed",
        "charge.dispute.funds_withdrawn",
        "charge.dispute.funds_reinstated",
    }
)


def _policy() -> fin.DisputePolicy:
    p = finance_policy().dispute
    if p is None:
        raise OpsError("DISPUTE_POLICY_ABSENT", "the signed finance policy names no dispute roles")
    return p


async def _event(
    conn: AsyncConnection,
    dispute_id: Any,
    kind: str,
    actor: str | None,
    detail: dict[str, Any],
    now: datetime,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO commercial.dispute_event (event_id, dispute_id, kind, actor, detail, at) VALUES (:e, :d, :k, :a, CAST(:x AS jsonb), :n)"
        ),
        {
            "e": uuid.uuid4(),
            "d": dispute_id,
            "k": kind,
            "a": uuid.UUID(actor) if actor else None,
            "x": json.dumps(detail, sort_keys=True),
            "n": now,
        },
    )


async def _load(conn: AsyncConnection, dispute_id: str, *, lock: bool = False) -> Any:
    row = (
        (
            await conn.execute(
                text(
                    "SELECT * FROM commercial.dispute_case WHERE dispute_id = :d"
                    + (" FOR UPDATE" if lock else "")
                ),
                {"d": uuid.UUID(dispute_id)},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise OpsError("DISPUTE_NOT_FOUND", "no such dispute case")
    return row


async def _payment(conn: AsyncConnection, payment_intent: str | None) -> Any:
    if not payment_intent:
        return None
    return (
        (
            await conn.execute(
                text(
                    "SELECT a.attempt_id, a.tenant_id, a.invoice_id, i.issuing_entity FROM commercial.recon_item r JOIN commercial.payment_attempt a ON a.attempt_id = r.item_id "
                    "JOIN commercial.invoice i ON i.invoice_id = a.invoice_id WHERE r.kind = 'PAYMENT' AND r.ref = :p"
                ),
                {"p": payment_intent},
            )
        )
        .mappings()
        .first()
    )


async def ingest(
    conn: AsyncConnection,
    *,
    event_type: str,
    obj: dict[str, Any],
    livemode: bool,
    merchant_ref: str = "",
    now: datetime | None = None,
) -> tuple[str, bool]:
    """Apply one verified dispute event. Returns (case id, created). Idempotent: the case is keyed by dispute, merchant and live mode."""
    now = now or datetime.now(UTC)
    ref = str(obj.get("id") or "")
    if not ref:
        raise OpsError("DISPUTE_MALFORMED", "a dispute event names its dispute")
    status = str(obj.get("status") or "unknown")
    pi = obj.get("payment_intent") if isinstance(obj.get("payment_intent"), str) else None
    amount = int(obj["amount"]) if isinstance(obj.get("amount"), int) else 0
    currency = str(obj.get("currency") or "usd").upper()
    due = (obj.get("evidence_details") or {}).get("due_by")
    due_by = datetime.fromtimestamp(int(due), UTC) if isinstance(due, int) else None
    pay = await _payment(conn, pi)
    existing: RowMapping | dict[str, Any] | None = (
        (
            await conn.execute(
                text(
                    "SELECT dispute_id, state, strategy FROM commercial.dispute_case WHERE provider_dispute_ref = :r AND merchant_ref = :m AND livemode = :l FOR UPDATE"
                ),
                {"r": ref, "m": merchant_ref, "l": livemode},
            )
        )
        .mappings()
        .first()
    )
    if existing is None:
        did = uuid.uuid4()
        await conn.execute(
            text(
                "INSERT INTO commercial.dispute_case (dispute_id, provider_dispute_ref, merchant_ref, livemode, tenant_id, invoice_id, issuing_entity, payment_attempt_id, payment_intent_ref, reason, amount_minor, "
                "currency, provider_status, evidence_due_by, state, last_synced_at, created_at, updated_at) VALUES (:d, :r, :m, :l, :t, :i, :e, :a, :p, :why, :amt, :c, :s, :due, 'OPEN', :n, :n, :n)"
            ),
            {
                "d": did,
                "r": ref,
                "m": merchant_ref,
                "l": livemode,
                "t": pay["tenant_id"] if pay else None,
                "i": pay["invoice_id"] if pay else None,
                "e": pay["issuing_entity"] if pay else None,
                "a": pay["attempt_id"] if pay else None,
                "p": pi,
                "why": str(obj.get("reason") or "unknown")[:80],
                "amt": amount,
                "c": currency,
                "s": status[:60],
                "due": due_by,
                "n": now,
            },
        )
        await _event(
            conn, did, "OPENED", None, {"provider_status": status, "event": event_type}, now
        )
        await log_change(
            conn,
            kind="DISPUTE_OPENED",
            ref=str(did),
            tenant_id=str(pay["tenant_id"]) if pay else None,
            actor=None,
            reason=str(obj.get("reason") or "")[:60],
        )
        existing = {"dispute_id": did, "state": "OPEN", "strategy": None}
        created = True
    else:
        created = False
        await conn.execute(
            text(
                "UPDATE commercial.dispute_case SET provider_status = :s, evidence_due_by = coalesce(:due, evidence_due_by), amount_minor = :amt, last_synced_at = :n, updated_at = :n WHERE dispute_id = :d"
            ),
            {"d": existing["dispute_id"], "s": status[:60], "due": due_by, "amt": amount, "n": now},
        )
        await _event(
            conn,
            existing["dispute_id"],
            "SYNCED",
            None,
            {"provider_status": status, "event": event_type},
            now,
        )
    did = existing["dispute_id"]
    if event_type == "charge.dispute.funds_withdrawn":
        await conn.execute(
            text(
                "UPDATE commercial.dispute_case SET funds_withdrawn_minor = :a WHERE dispute_id = :d"
            ),
            {"d": did, "a": amount},
        )
        await conn.execute(
            text(
                "INSERT INTO commercial.recon_item (item_id, kind, tenant_id, issuing_entity, currency, amount_minor, ref, journal_ref, occurred_on) "
                "SELECT :d, 'DISPUTE', tenant_id, issuing_entity, :c, :a, :r, '', :day FROM commercial.dispute_case WHERE dispute_id = :d AND tenant_id IS NOT NULL AND issuing_entity IS NOT NULL "
                "ON CONFLICT (kind, ref) DO NOTHING"
            ),
            {"d": did, "c": currency, "a": -amount, "r": ref, "day": now.date()},
        )
    closed = ops.provider_dispute_close(status, accepted_by_us=existing["strategy"] == "ACCEPT")
    if closed is not None and DisputeState(existing["state"]) not in ops.DISPUTE_FINAL:
        fee = None
        for bt in obj.get("balance_transactions") or []:
            if isinstance(bt, dict) and isinstance(bt.get("fee"), int):
                fee = (fee or 0) + int(bt["fee"])
        await conn.execute(
            text(
                "UPDATE commercial.dispute_case SET state = :s, outcome = :o, fee_minor = coalesce(:f, fee_minor), updated_at = :n WHERE dispute_id = :d"
            ),
            {"d": did, "s": closed.value, "o": status[:60], "f": fee, "n": now},
        )
        await _event(
            conn,
            did,
            "CLOSED_BY_PROCESSOR",
            None,
            {"provider_status": status, "state": closed.value},
            now,
        )
        await log_change(
            conn,
            kind="DISPUTE_CLOSED",
            ref=str(did),
            tenant_id=None,
            actor=None,
            reason=closed.value,
        )
    return str(did), created


async def assign(
    conn: AsyncConnection, dispute_id: str, *, owner: str, by: str, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    p = _policy()
    row = await _load(conn, dispute_id, lock=True)
    if DisputeState(row["state"]) in ops.DISPUTE_FINAL:
        raise OpsError("DISPUTE_CLOSED", "a closed dispute is not reassigned")
    if not any([await finance.holds(conn, by, r, now) for r in (*p.owner_roles, *p.decide_roles)]):
        raise fin.FinanceError(
            "NOT_A_DISPUTES_LEAD",
            "only an appointed Disputes Specialist or a deciding role assigns a case",
        )
    if not any([await finance.holds(conn, owner, r, now) for r in p.owner_roles]):
        raise fin.FinanceError(
            "OWNER_NOT_A_SPECIALIST", "a case is owned by an appointed Disputes Specialist"
        )
    await conn.execute(
        text(
            "UPDATE commercial.dispute_case SET owner = :o, updated_at = :n WHERE dispute_id = :d"
        ),
        {"d": row["dispute_id"], "o": uuid.UUID(owner), "n": now},
    )
    await _event(conn, row["dispute_id"], "ASSIGNED", by, {"owner": owner}, now)
    return await view(conn, dispute_id, now=now)


async def add_evidence(
    conn: AsyncConnection,
    dispute_id: str,
    *,
    item: str,
    ref: str,
    by: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Add one checklist item to the evidence packet by reference (the documents stay where they are kept, redacted). Each change is a new packet version."""
    now = now or datetime.now(UTC)
    p = _policy()
    if item not in ops.EVIDENCE_ITEMS:
        raise OpsError("EVIDENCE_ITEM_UNKNOWN", item)
    if not ref.strip():
        raise OpsError("EVIDENCE_REF_REQUIRED", "name where the evidence is kept")
    row = await _load(conn, dispute_id, lock=True)
    if DisputeState(row["state"]) in ops.DISPUTE_FINAL:
        raise OpsError("DISPUTE_CLOSED", "a closed dispute takes no more evidence")
    if not any([await finance.holds(conn, by, r, now) for r in (*p.owner_roles, *p.decide_roles)]):
        raise fin.FinanceError(
            "NOT_A_DISPUTES_LEAD", "only the owner or a deciding role adds evidence"
        )
    packet = dict(row["evidence"] or {})
    packet[item] = {"ref": ref.strip()[:200], "by": by, "at": now.isoformat()}
    state = DisputeState(row["state"])
    new_state = (
        ops.dispute_transition(state, DisputeState.INVESTIGATING)
        if state is DisputeState.OPEN
        else state
    )
    await conn.execute(
        text(
            "UPDATE commercial.dispute_case SET evidence = CAST(:e AS jsonb), evidence_version = evidence_version + 1, state = :s, updated_at = :n WHERE dispute_id = :d"
        ),
        {
            "d": row["dispute_id"],
            "e": json.dumps(packet, sort_keys=True),
            "s": new_state.value,
            "n": now,
        },
    )
    await _event(conn, row["dispute_id"], "EVIDENCE_ADDED", by, {"item": item}, now)
    return await view(conn, dispute_id, now=now)


async def decide(
    conn: AsyncConnection,
    dispute_id: str,
    *,
    strategy: str,
    reason: str,
    by: str,
    environment: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Accept or contest. A finance decision: the deciding role, a signed policy, a complete evidence packet to contest, and no overlap with money already returned."""
    now = now or datetime.now(UTC)
    p = _policy()
    if strategy not in ("ACCEPT", "CONTEST"):
        raise OpsError("STRATEGY_INVALID", strategy)
    if not reason.strip():
        raise OpsError("REASON_REQUIRED", "say why")
    await finance.require_effective(conn, environment)
    row = await _load(conn, dispute_id, lock=True)
    state = DisputeState(row["state"])
    if not any([await finance.holds(conn, by, r, now) for r in p.decide_roles]):
        raise fin.FinanceError(
            "NOT_A_DECIDER", "only an appointed Controller or CFO accepts or contests a dispute"
        )
    if row["owner"] is not None and str(row["owner"]) == by:
        raise fin.FinanceError(
            "OWNER_CANNOT_DECIDE", "the person who owns the case does not also decide it"
        )
    if strategy == "CONTEST" and not ops.checklist_complete(row["evidence"] or {}):
        raise OpsError(
            "EVIDENCE_INCOMPLETE",
            "the evidence packet is missing items; a dispute is contested only with the full checklist",
        )
    overlap = (
        (
            await conn.execute(
                text(
                    "SELECT count(*) FROM commercial.refund_case WHERE payment_attempt_id = :a AND state IN ('EXECUTION_PENDING', 'PROCESSOR_PENDING', 'PARTIAL', 'SUCCEEDED', 'UNKNOWN')"
                ),
                {"a": row["payment_attempt_id"]},
            )
        ).scalar_one()
        if row["payment_attempt_id"]
        else 0
    )
    if strategy == "CONTEST" and overlap:
        raise OpsError(
            "DISPUTE_OVERLAPS_REFUND",
            "money for this payment has already been, or is being, refunded: contesting would return it twice",
        )
    if state in (DisputeState.OPEN, DisputeState.INVESTIGATING):
        state = ops.dispute_transition(state, DisputeState.DECISION_PENDING)
    target = ops.dispute_transition(
        state, DisputeState.ACCEPTED if strategy == "ACCEPT" else DisputeState.CONTESTING
    )
    await conn.execute(
        text(
            "UPDATE commercial.dispute_case SET state = :s, strategy = :g, strategy_by = :b, strategy_at = :n, strategy_reason = :r, updated_at = :n WHERE dispute_id = :d"
        ),
        {
            "d": row["dispute_id"],
            "s": target.value,
            "g": strategy,
            "b": uuid.UUID(by),
            "n": now,
            "r": reason.strip()[:500],
        },
    )
    await _event(
        conn,
        row["dispute_id"],
        "DECIDED",
        by,
        {"strategy": strategy, "overlapping_refunds": int(overlap)},
        now,
    )
    await log_change(
        conn,
        kind="DISPUTE_DECIDED",
        ref=dispute_id,
        tenant_id=str(row["tenant_id"]) if row["tenant_id"] else None,
        actor=by,
        reason=strategy,
    )
    return await view(conn, dispute_id, now=now)


async def record_submission(
    conn: AsyncConnection, dispute_id: str, *, reference: str, by: str, now: datetime | None = None
) -> dict[str, Any]:
    """Record that a separately appointed processor operator handed the evidence to the processor's dashboard. This is what a person says: it is SUBMISSION_RECORDED,
    not submitted, and not won, until the processor confirms it."""
    now = now or datetime.now(UTC)
    p = _policy()
    row = await _load(conn, dispute_id, lock=True)
    if not any([await finance.holds(conn, by, r, now) for r in p.submit_roles]):
        raise fin.FinanceError(
            "NOT_A_PROCESSOR_OPERATOR",
            "only a separately appointed processor operator records the hand-off",
        )
    if not reference.strip():
        raise OpsError(
            "SUBMISSION_REF_REQUIRED", "record the processor's own reference for the submission"
        )
    if DisputeState(row["state"]) is not DisputeState.CONTESTING:
        raise OpsError(
            "NOT_CONTESTING",
            "only a dispute the finance decision chose to contest is handed to the processor",
        )
    if row["evidence_due_by"] is not None and row["evidence_due_by"] <= now:
        raise OpsError(
            "DEADLINE_PASSED",
            "the processor's deadline has passed: escalate, do not record a submission",
        )
    target = ops.dispute_transition(DisputeState.CONTESTING, DisputeState.SUBMISSION_RECORDED)
    await conn.execute(
        text(
            "UPDATE commercial.dispute_case SET state = :s, submission_ref = :r, submitted_by = :b, submission_recorded_at = :n, updated_at = :n WHERE dispute_id = :d"
        ),
        {
            "d": row["dispute_id"],
            "s": target.value,
            "r": reference.strip()[:120],
            "b": uuid.UUID(by),
            "n": now,
        },
    )
    await _event(
        conn,
        row["dispute_id"],
        "SUBMISSION_RECORDED",
        by,
        {"reference": reference.strip()[:120]},
        now,
    )
    return await view(conn, dispute_id, now=now)


async def verify_submission(
    conn: AsyncConnection, dispute_id: str, port: RefundPort, *, now: datetime | None = None
) -> dict[str, Any]:
    """Ask the processor whether it really holds a submission for this dispute. Only its answer moves the case to SUBMISSION_VERIFIED."""
    now = now or datetime.now(UTC)
    row = await _load(conn, dispute_id, lock=True)
    if DisputeState(row["state"]) is not DisputeState.SUBMISSION_RECORDED:
        raise OpsError("NOT_RECORDED", "there is no recorded submission to verify")
    doc = await port.retrieve_dispute(row["provider_dispute_ref"])
    if doc is None:
        raise OpsError("PROCESSOR_UNAVAILABLE", "the processor could not be asked: nothing changed")
    details = doc.get("evidence_details") or {}
    held = int(details.get("submission_count") or 0) > 0 or bool(details.get("has_evidence"))
    status = str(doc.get("status") or "unknown")
    await conn.execute(
        text(
            "UPDATE commercial.dispute_case SET provider_status = :s, last_synced_at = :n, updated_at = :n WHERE dispute_id = :d"
        ),
        {"d": row["dispute_id"], "s": status[:60], "n": now},
    )
    if not held:
        await _event(
            conn, row["dispute_id"], "SUBMISSION_NOT_SEEN", None, {"provider_status": status}, now
        )
        raise OpsError(
            "NOT_YET_AT_PROCESSOR", "the processor does not show a submission for this dispute yet"
        )
    target = ops.dispute_transition(
        DisputeState.SUBMISSION_RECORDED, DisputeState.SUBMISSION_VERIFIED
    )
    await conn.execute(
        text(
            "UPDATE commercial.dispute_case SET state = :s, submission_verified_at = :n WHERE dispute_id = :d"
        ),
        {"d": row["dispute_id"], "s": target.value, "n": now},
    )
    await _event(
        conn, row["dispute_id"], "SUBMISSION_VERIFIED", None, {"provider_status": status}, now
    )
    return await view(conn, dispute_id, now=now)


async def view(
    conn: AsyncConnection, dispute_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    r = await _load(conn, dispute_id)
    events = (
        (
            await conn.execute(
                text(
                    "SELECT kind, actor, detail, at FROM commercial.dispute_event WHERE dispute_id = :d ORDER BY at"
                ),
                {"d": r["dispute_id"]},
            )
        )
        .mappings()
        .all()
    )
    refunds = (
        (
            await conn.execute(
                text(
                    "SELECT state, amount_minor FROM commercial.refund_case WHERE payment_attempt_id = :a"
                ),
                {"a": r["payment_attempt_id"]},
            )
        ).all()
        if r["payment_attempt_id"]
        else []
    )
    evidence = r["evidence"] or {}
    return {
        "dispute_id": str(r["dispute_id"]),
        "provider_dispute_ref": r["provider_dispute_ref"],
        "merchant_ref": r["merchant_ref"],
        "livemode": r["livemode"],
        "tenant_id": str(r["tenant_id"]) if r["tenant_id"] else None,
        "invoice_id": str(r["invoice_id"]) if r["invoice_id"] else None,
        "issuing_entity": r["issuing_entity"],
        "payment_intent_ref": r["payment_intent_ref"],
        "reason": r["reason"],
        "amount_minor": int(r["amount_minor"]),
        "currency": r["currency"],
        "provider_status": r["provider_status"],
        "deadline": ops.deadline_view(r["evidence_due_by"], now, zone=r["deadline_zone"]),
        "state": r["state"],
        "owner": str(r["owner"]) if r["owner"] else None,
        "strategy": r["strategy"],
        "strategy_reason": r["strategy_reason"],
        "evidence": {
            "version": int(r["evidence_version"]),
            "items": {
                k: {"present": k in evidence, "ref": (evidence.get(k) or {}).get("ref")}
                for k in ops.EVIDENCE_ITEMS
            },
            "complete": ops.checklist_complete(evidence),
        },
        "submission": {
            "reference": r["submission_ref"],
            "recorded_at": r["submission_recorded_at"].isoformat()
            if r["submission_recorded_at"]
            else None,
            "verified_at": r["submission_verified_at"].isoformat()
            if r["submission_verified_at"]
            else None,
        },
        "outcome": {
            "provider_status": r["outcome"],
            "fee_minor": r["fee_minor"],
            "funds_withdrawn_minor": int(r["funds_withdrawn_minor"]),
        },
        "overlap": {"refunds": [{"state": s, "amount_minor": int(a)} for s, a in refunds]},
        "timeline": [
            {
                "kind": e["kind"],
                "actor": str(e["actor"]) if e["actor"] else None,
                "detail": e["detail"],
                "at": e["at"].isoformat(),
            }
            for e in events
        ],
        "last_synced_at": r["last_synced_at"].isoformat(),
    }


async def list_cases(
    conn: AsyncConnection, *, open_only: bool = False, limit: int = 100
) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT dispute_id FROM commercial.dispute_case WHERE (NOT :o OR state <> ALL(:f)) ORDER BY evidence_due_by NULLS LAST, created_at DESC LIMIT :n"
            ),
            {"o": open_only, "f": [s.value for s in ops.DISPUTE_FINAL], "n": limit},
        )
    ).all()
    return [await view(conn, str(r[0])) for r in rows]
