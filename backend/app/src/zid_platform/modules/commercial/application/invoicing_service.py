"""Invoices, credit notes, payments and billing state (ZID-COM-001 §11, §13, §30, §31; CR-027).

Every function runs on the caller's connection under the tenant's RLS scope, except the issuing entity's
sequence and chain, which are platform rows holding numbers and digests only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.application import activation_service as activations
from zid_platform.modules.commercial.application import events
from zid_platform.modules.commercial.application.adapters import (
    PaymentPort,
    TaxableLine,
    TaxPort,
)
from zid_platform.modules.commercial.application.change_log import log_change
from zid_platform.modules.commercial.domain import invoicing as inv

ZERO = Decimal(0)


def _d(v: Decimal | None) -> str | None:
    return None if v is None else format(Decimal(v).normalize(), "f")


def _q(v: Decimal) -> Decimal:
    return Decimal(v).quantize(Decimal("0.01"))


# ------------------------------------------------------------------------------- billing state


async def billing_state(conn: AsyncConnection) -> dict[str, Any]:
    row = (
        (await conn.execute(text("SELECT * FROM commercial.billing_account_state")))
        .mappings()
        .first()
    )
    state = row["state"] if row else inv.DEFAULT_STATE
    hold = bool(row["safety_hold"]) if row else False
    return {
        "state": state,
        "reason": row["reason_code"] if row else "DEFAULT",
        "safety_hold": hold,
        "since": row["since"].isoformat() if row else None,
        "version": row["version"] if row else 0,
        "restricted": sorted(inv.restricted(state, hold)),
        "never_restricted": list(inv.NEVER_RESTRICTED),
    }


async def transition(
    conn: AsyncConnection,
    tenant_id: str,
    target: str,
    reason: str,
    *,
    actor: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    row = (
        (
            await conn.execute(
                text("SELECT state, version FROM commercial.billing_account_state FOR UPDATE")
            )
        )
        .mappings()
        .first()
    )
    current = row["state"] if row else inv.DEFAULT_STATE
    inv.check_transition(current, target, reason)
    if row is None:
        await conn.execute(
            text(
                "INSERT INTO commercial.billing_account_state (tenant_id, state, reason_code, since) "
                "VALUES (:t, :s, :r, :n)"
            ),
            {"t": uuid.UUID(tenant_id), "s": target, "r": reason, "n": now},
        )
        version = 1
    else:
        version = int(row["version"]) + 1
        await conn.execute(
            text(
                "UPDATE commercial.billing_account_state SET state = :s, reason_code = :r, since = :n, "
                "version = :v"
            ),
            {"s": target, "r": reason, "n": now, "v": version},
        )
    await conn.execute(
        text(
            "INSERT INTO commercial.billing_state_history (entry_id, tenant_id, from_state, to_state, "
            "reason_code, actor, at) VALUES (:i, :t, :f, :s, :r, :a, :n)"
        ),
        {
            "i": uuid.uuid4(),
            "t": uuid.UUID(tenant_id),
            "f": current,
            "s": target,
            "r": reason,
            "a": uuid.UUID(actor) if actor else None,
            "n": now,
        },
    )
    await events.billing_state_changed(
        conn,
        tenant_id=tenant_id,
        version=version,
        from_state=current,
        to_state=target,
        reason=reason,
    )
    await log_change(
        conn,
        kind="BILLING_STATE_CHANGED",
        ref=tenant_id,
        tenant_id=tenant_id,
        actor=actor,
        reason=reason,
        detail={"from": current, "to": target, "version": version},
    )
    return {"from": current, "to": target, "reason": reason, "version": version}


async def set_safety_hold(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    on: bool,
    reason: str | None,
    now: datetime | None = None,
) -> None:
    """A safety hold is not a billing state: it is set and cleared on its own, and paying never clears it."""
    now = now or datetime.now(UTC)
    if on and not reason:
        raise inv.InvoicingError("SAFETY_REASON_REQUIRED")
    await conn.execute(
        text(
            "INSERT INTO commercial.billing_account_state (tenant_id, state, reason_code, safety_hold, "
            "safety_reason, since) VALUES (:t, :s, 'DEFAULT', :h, :r, :n) "
            "ON CONFLICT (tenant_id) DO UPDATE SET safety_hold = EXCLUDED.safety_hold, "
            "safety_reason = EXCLUDED.safety_reason, version = commercial.billing_account_state.version + 1"
        ),
        {
            "t": uuid.UUID(tenant_id),
            "s": inv.DEFAULT_STATE,
            "h": on,
            "r": reason if on else None,
            "n": now,
        },
    )


# ------------------------------------------------------------------------------- invoices


async def _computed_run(conn: AsyncConnection, month: Any) -> dict[str, Any]:
    run = (
        (
            await conn.execute(
                text(
                    "SELECT * FROM commercial.rating_run WHERE period = :p AND state = 'COMPUTED'"
                ),
                {"p": month},
            )
        )
        .mappings()
        .first()
    )
    if run is None:
        raise inv.InvoicingError("RATING_NOT_FOUND", "rate the month before invoicing it")
    return dict(run)


async def load_invoice(
    conn: AsyncConnection, invoice_id: str, *, lock: bool = False
) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(
                    "SELECT * FROM commercial.invoice WHERE invoice_id = :i"
                    + (" FOR UPDATE" if lock else "")
                ),
                {"i": uuid.UUID(invoice_id)},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise inv.InvoicingError("INVOICE_NOT_FOUND")
    return dict(row)


async def invoice_lines(conn: AsyncConnection, invoice_id: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text("SELECT * FROM commercial.invoice_line WHERE invoice_id = :i ORDER BY seq"),
            {"i": uuid.UUID(invoice_id)},
        )
    ).mappings()
    return [dict(r) for r in rows]


async def create_draft(
    conn: AsyncConnection, *, tenant_id: str, month: Any, issuing_entity: str, actor: str
) -> dict[str, Any]:
    """A DRAFT from the month's COMPUTED rating run. Idempotent: the live invoice of a run is returned."""
    run = await _computed_run(conn, month)
    existing = (
        (
            await conn.execute(
                text(
                    "SELECT invoice_id FROM commercial.invoice WHERE rating_run_id = :r AND status <> 'VOID'"
                ),
                {"r": run["run_id"]},
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return await view(conn, str(existing))
    invoice_id = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO commercial.invoice (invoice_id, tenant_id, period, rating_run_id, issuing_entity, "
            "currency, subtotal, gross, tax_status, status, created_by) VALUES (:i, :t, :p, :r, :e, :c, :s, :s, "
            "'NOT_CONFIGURED', 'DRAFT', :a)"
        ),
        {
            "i": invoice_id,
            "t": uuid.UUID(tenant_id),
            "p": month,
            "r": run["run_id"],
            "e": issuing_entity,
            "c": run["currency"],
            "s": run["subtotal"],
            "a": uuid.UUID(actor),
        },
    )
    rated = (
        await conn.execute(
            text("SELECT * FROM commercial.rated_usage WHERE run_id = :r ORDER BY seq"),
            {"r": run["run_id"]},
        )
    ).mappings()
    last_seq = 0
    for ln in rated:
        last_seq = max(last_seq, int(ln["seq"]))
        await conn.execute(
            text(
                "INSERT INTO commercial.invoice_line (line_id, invoice_id, tenant_id, seq, kind, sku, meter_id, "
                "description, included_units, chargeable_units, unit_rate, net, gross) VALUES (:l, :i, :t, :q, "
                ":k, :s, :m, :d, :inc, :ch, :u, :n, :n)"
            ),
            {
                "l": uuid.uuid4(),
                "i": invoice_id,
                "t": uuid.UUID(tenant_id),
                "q": ln["seq"],
                "k": ln["kind"],
                "s": ln["sku"],
                "m": ln["meter"],
                "d": ln["formula"],
                "inc": ln["included"],
                "ch": ln["chargeable"],
                "u": ln["unit_rate"],
                "n": ln["net"],
            },
        )
    # A plan paid for in advance on activation (CR-062) is taken off the invoices it fits on, so the fee is not billed twice. The platform fee of a part month is
    # prorated by the rating run while the company paid a whole month, so what does not fit on this invoice carries to the next one.
    remaining = Decimal(run["subtotal"])
    for credit in await activations.unapplied_credits(conn, tenant_id, run["currency"]):
        take = min(Decimal(credit["balance"]), remaining)
        if take <= 0:
            continue
        last_seq += 1
        await conn.execute(
            text(
                "INSERT INTO commercial.invoice_line (line_id, invoice_id, tenant_id, seq, kind, sku, description, net, gross) "
                "VALUES (:l, :i, :t, :q, 'CREDIT', 'ZID-BASE', :d, :n, :n)"
            ),
            {
                "l": uuid.uuid4(),
                "i": invoice_id,
                "t": uuid.UUID(tenant_id),
                "q": last_seq,
                "d": "Platform fee paid in advance when the plan was activated",
                "n": -take,
            },
        )
        remaining -= take
        await activations.apply_credit(conn, tenant_id, credit["order_id"], invoice_id, take)
    if remaining != Decimal(run["subtotal"]):
        await conn.execute(
            text("UPDATE commercial.invoice SET subtotal = :s, gross = :s WHERE invoice_id = :i"),
            {"s": remaining, "i": invoice_id},
        )
    return await view(conn, str(invoice_id))


async def _next_number(conn: AsyncConnection, entity: str) -> tuple[int, str | None]:
    """The next gap-free number of an issuing entity; the row stays locked until the transaction ends."""
    await conn.execute(
        text(
            "INSERT INTO commercial.invoice_sequence (issuing_entity) VALUES (:e) ON CONFLICT DO NOTHING"
        ),
        {"e": entity},
    )
    row = (
        await conn.execute(
            text(
                "SELECT last_number, last_chain_hash FROM commercial.invoice_sequence "
                "WHERE issuing_entity = :e FOR UPDATE"
            ),
            {"e": entity},
        )
    ).one()
    return int(row[0]) + 1, row[1]


async def _link(
    conn: AsyncConnection,
    entity: str,
    number: int,
    previous: str | None,
    doc_digest: str,
    kind: str,
) -> str:
    link = inv.chain(previous, number, doc_digest)
    await conn.execute(
        text(
            "INSERT INTO commercial.invoice_chain (issuing_entity, sequence_number, document_digest, chain_hash, "
            "kind) VALUES (:e, :n, :d, :c, :k)"
        ),
        {"e": entity, "n": number, "d": doc_digest, "c": link, "k": kind},
    )
    await conn.execute(
        text(
            "UPDATE commercial.invoice_sequence SET last_number = :n, last_chain_hash = :c "
            "WHERE issuing_entity = :e"
        ),
        {"n": number, "c": link, "e": entity},
    )
    return link


def _document(
    i: dict[str, Any], lines: list[dict[str, Any]], number: int, tenant_alias: str
) -> str:
    return inv.render(
        issuing_entity=i["issuing_entity"],
        sequence_number=number,
        tenant_alias=tenant_alias,
        period=i["period"].strftime("%Y-%m"),
        currency=i["currency"],
        issued_at=i["issued_at"],
        due_at=i["due_at"],
        lines=[
            inv.DocLine(
                ln["seq"],
                ln["kind"],
                ln["description"],
                Decimal(ln["net"]),
                Decimal(ln["tax_amount"]),
                ln["tax_code"],
            )
            for ln in lines
        ],
        subtotal=Decimal(i["subtotal"]),
        tax_total=Decimal(i["tax_total"]),
        gross=Decimal(i["gross"]),
        tax_status=i["tax_status"],
    )


def tenant_alias(tenant_id: str) -> str:
    """The customer-visible tenant alias on documents (§30): never the raw tenant id."""
    return "T-" + uuid.UUID(tenant_id).hex[:12].upper()


async def account_country(conn: AsyncConnection, tenant_id: str) -> str | None:
    """The buyer's country from the commercial account that owns this tenant (for the tax engine)."""
    return (
        await conn.execute(
            text(
                "SELECT a.country FROM commercial.account a JOIN commercial.account_tenant t "
                "ON t.account_id = a.account_id WHERE t.tenant_id = :t AND t.effective_to IS NULL"
            ),
            {"t": uuid.UUID(tenant_id)},
        )
    ).scalar_one_or_none()


async def payment_customer(conn: AsyncConnection, tenant_id: str) -> str | None:
    """The processor's customer id for the account that owns this tenant: a reference, never card data."""
    return (
        await conn.execute(
            text(
                "SELECT c.customer_ref FROM commercial.payment_customer c JOIN commercial.account_tenant t "
                "ON t.account_id = c.account_id WHERE t.tenant_id = :t AND t.effective_to IS NULL"
            ),
            {"t": uuid.UUID(tenant_id)},
        )
    ).scalar_one_or_none()


async def account_of_tenant(conn: AsyncConnection, tenant_id: str) -> dict[str, str] | None:
    """The commercial account that currently owns this tenant (the operator creates and links it with the order)."""
    row = (
        (
            await conn.execute(
                text(
                    "SELECT a.account_id, a.legal_name FROM commercial.account a JOIN commercial.account_tenant t "
                    "ON t.account_id = a.account_id WHERE t.tenant_id = :t AND t.effective_to IS NULL"
                ),
                {"t": uuid.UUID(tenant_id)},
            )
        )
        .mappings()
        .first()
    )
    return (
        {"account_id": str(row["account_id"]), "legal_name": str(row["legal_name"])}
        if row
        else None
    )


async def set_payment_customer(
    conn: AsyncConnection, account_id: str, *, provider: str, customer_ref: str, actor: str
) -> None:
    await conn.execute(
        text(
            "INSERT INTO commercial.payment_customer (account_id, provider, customer_ref, set_by) "
            "VALUES (:a, :p, :c, :u) ON CONFLICT (account_id) DO UPDATE SET provider = EXCLUDED.provider, "
            "customer_ref = EXCLUDED.customer_ref, set_by = EXCLUDED.set_by, set_at = now()"
        ),
        {"a": uuid.UUID(account_id), "p": provider, "c": customer_ref, "u": uuid.UUID(actor)},
    )


async def issue(
    conn: AsyncConnection,
    invoice_id: str,
    *,
    issuer: str,
    tax: TaxPort,
    payment_terms_days: int,
    allow_zero_tax: bool,
    region: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    i = await load_invoice(conn, invoice_id, lock=True)
    if i["status"] != "DRAFT":
        raise inv.InvoicingError("INVOICE_NOT_DRAFT")
    if str(i["created_by"]) == issuer:
        raise inv.InvoicingError("INVOICE_SELF_ISSUE", "a second operator issues the invoice")
    if not 0 <= payment_terms_days <= 365:
        raise inv.InvoicingError("PAYMENT_TERMS_INVALID")
    lines = await invoice_lines(conn, invoice_id)
    taxed = await tax.lines_for(
        currency=i["currency"],
        region=region,
        country=await account_country(conn, str(i["tenant_id"])),
        lines=[TaxableLine(ln["seq"], ln["kind"], ln["sku"], Decimal(ln["net"])) for ln in lines],
    )
    if taxed is None and not allow_zero_tax:
        raise inv.InvoicingError(
            "TAX_NOT_CONFIGURED",
            "no tax engine answered; issue only with an explicit zero-tax override",
        )
    tax_status = "CONFIGURED" if taxed is not None else "ZERO_TAX_OVERRIDE"
    tax_total = ZERO
    for ln in lines:
        t = (taxed or {}).get(ln["seq"])
        amount = _q(t.amount) if t else ZERO
        tax_total += amount
        await conn.execute(
            text(
                "UPDATE commercial.invoice_line SET tax_code = :c, tax_amount = :a, gross = net + :a "
                "WHERE line_id = :l"
            ),
            {"c": t.code if t else None, "a": amount, "l": ln["line_id"]},
        )
    gross = Decimal(i["subtotal"]) + tax_total
    number, previous = await _next_number(conn, i["issuing_entity"])
    i.update(
        tax_total=tax_total,
        gross=gross,
        tax_status=tax_status,
        issued_at=now,
        due_at=now + timedelta(days=payment_terms_days),
    )
    doc = _document(
        i, await invoice_lines(conn, invoice_id), number, tenant_alias(str(i["tenant_id"]))
    )
    doc_digest = inv.digest(doc)
    link = await _link(conn, i["issuing_entity"], number, previous, doc_digest, "INVOICE")
    await conn.execute(
        text(
            "UPDATE commercial.invoice SET status = 'ISSUED', sequence_number = :n, issued_at = :ia, due_at = :d, "
            "issued_by = :b, tax_total = :tt, gross = :g, tax_status = :ts, document_digest = :dg, chain_hash = :c "
            "WHERE invoice_id = :i"
        ),
        {
            "n": number,
            "ia": now,
            "d": i["due_at"],
            "b": uuid.UUID(issuer),
            "tt": tax_total,
            "g": gross,
            "ts": tax_status,
            "dg": doc_digest,
            "c": link,
            "i": uuid.UUID(invoice_id),
        },
    )
    await events.invoice_issued(
        conn,
        tenant_id=str(i["tenant_id"]),
        invoice_id=invoice_id,
        number=f"{i['issuing_entity']}-{number:08d}",
        period=i["period"].strftime("%Y-%m"),
    )
    await log_change(
        conn,
        kind="INVOICE_ISSUED",
        ref=invoice_id,
        tenant_id=str(i["tenant_id"]),
        actor=issuer,
        detail={"number": number, "document_digest": doc_digest, "gross": str(gross)},
    )
    return await view(conn, invoice_id)


async def void(conn: AsyncConnection, invoice_id: str) -> None:
    i = await load_invoice(conn, invoice_id, lock=True)
    if i["status"] != "DRAFT":
        raise inv.InvoicingError(
            "INVOICE_NOT_DRAFT", "an issued invoice is corrected by a credit note"
        )
    await conn.execute(
        text("UPDATE commercial.invoice SET status = 'VOID' WHERE invoice_id = :i"),
        {"i": uuid.UUID(invoice_id)},
    )
    await activations.release_credits(
        conn, str(i["tenant_id"]), invoice_id
    )  # what it took off a paid activation is available again


async def document(conn: AsyncConnection, invoice_id: str) -> str:
    i = await load_invoice(conn, invoice_id)
    if i["status"] != "ISSUED":
        raise inv.InvoicingError("INVOICE_NOT_ISSUED")
    return _document(
        i,
        await invoice_lines(conn, invoice_id),
        int(i["sequence_number"]),
        tenant_alias(str(i["tenant_id"])),
    )


async def _credited(conn: AsyncConnection, invoice_id: str) -> Decimal:
    return Decimal(
        (
            await conn.execute(
                text(
                    "SELECT coalesce(sum(amount), 0) FROM commercial.credit_note WHERE invoice_id = :i"
                ),
                {"i": uuid.UUID(invoice_id)},
            )
        ).scalar_one()
    )


async def credit_headroom(conn: AsyncConnection, invoice_id: str) -> tuple[dict[str, Any], Decimal]:
    """The invoice and what is still creditable on it, so a finance request is refused before anyone is asked to approve it."""
    i = await load_invoice(conn, invoice_id)
    return i, Decimal(i["gross"]) - await _credited(conn, invoice_id)


async def amount_paid(conn: AsyncConnection, invoice_id: str) -> Decimal:
    return await _paid(conn, invoice_id)


async def _paid(conn: AsyncConnection, invoice_id: str) -> Decimal:
    return Decimal(
        (
            await conn.execute(
                text(
                    "SELECT coalesce(sum(amount), 0) FROM commercial.payment_attempt "
                    "WHERE invoice_id = :i AND status = 'CAPTURED'"
                ),
                {"i": uuid.UUID(invoice_id)},
            )
        ).scalar_one()
    )


async def credit_note(
    conn: AsyncConnection,
    invoice_id: str,
    *,
    amount: Decimal,
    reason_code: str,
    reverses_line: int | None,
    issuer: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """A credit note reverses part of an issued invoice; the invoice itself never changes (§31)."""
    now = now or datetime.now(UTC)
    i = await load_invoice(conn, invoice_id, lock=True)
    if i["status"] != "ISSUED":
        raise inv.InvoicingError("INVOICE_NOT_ISSUED", "only an issued invoice is credited")
    amount = _q(amount)
    inv.creditable(Decimal(i["gross"]), await _credited(conn, invoice_id), amount)
    number, previous = await _next_number(conn, i["issuing_entity"])
    doc = "\n".join(
        [
            "ZOIKO iD CREDIT NOTE",
            f"Issuer: {i['issuing_entity']}",
            f"Credit note: {i['issuing_entity']}-{number:08d}",
            f"Against invoice: {i['issuing_entity']}-{int(i['sequence_number']):08d}",
            f"Customer: {tenant_alias(str(i['tenant_id']))}",
            f"Reason: {reason_code}" + (f" (line {reverses_line:03d})" if reverses_line else ""),
            f"Amount: {amount:.2f} {i['currency']}",
            f"Issued: {now.date().isoformat()}",
            "",
        ]
    )
    doc_digest = inv.digest(doc)
    await _link(conn, i["issuing_entity"], number, previous, doc_digest, "CREDIT_NOTE")
    cid = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO commercial.credit_note (credit_note_id, tenant_id, invoice_id, issuing_entity, "
            "sequence_number, amount, reason_code, reverses_line, document_digest, issued_by, issued_at) "
            "VALUES (:c, :t, :i, :e, :n, :a, :r, :l, :d, :b, :w)"
        ),
        {
            "c": cid,
            "t": i["tenant_id"],
            "i": uuid.UUID(invoice_id),
            "e": i["issuing_entity"],
            "n": number,
            "a": amount,
            "r": reason_code,
            "l": reverses_line,
            "d": doc_digest,
            "b": uuid.UUID(issuer),
            "w": now,
        },
    )
    await events.credit_note_issued(
        conn, tenant_id=str(i["tenant_id"]), credit_note_id=str(cid), invoice_id=invoice_id
    )
    await log_change(
        conn,
        kind="CREDIT_NOTE_ISSUED",
        ref=str(cid),
        tenant_id=str(i["tenant_id"]),
        actor=issuer,
        reason=reason_code,
        detail={"invoice_id": invoice_id, "amount": str(amount), "document_digest": doc_digest},
    )
    return {
        "credit_note_id": str(cid),
        "number": f"{i['issuing_entity']}-{number:08d}",
        "amount": _d(amount),
        "document_digest": doc_digest,
    }


async def collect(
    conn: AsyncConnection,
    invoice_id: str,
    *,
    payments: PaymentPort,
    idempotency_key: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Collect what is outstanding. The same idempotency key never charges twice. A full payment returns a
    past-due, restricted or suspended account to ACTIVE, unless a safety hold is on."""
    now = now or datetime.now(UTC)
    i = await load_invoice(conn, invoice_id, lock=True)
    if i["status"] != "ISSUED":
        raise inv.InvoicingError("INVOICE_NOT_ISSUED")
    prior = (
        (
            await conn.execute(
                text("SELECT * FROM commercial.payment_attempt WHERE idempotency_key = :k"),
                {"k": idempotency_key},
            )
        )
        .mappings()
        .first()
    )
    if prior is not None:
        return {"attempt_id": str(prior["attempt_id"]), "status": prior["status"], "replayed": True}
    outstanding = (
        Decimal(i["gross"]) - await _credited(conn, invoice_id) - await _paid(conn, invoice_id)
    )
    if outstanding <= 0:
        raise inv.InvoicingError("INVOICE_SETTLED", "nothing is outstanding")
    result = await payments.capture(
        invoice_id=invoice_id,
        amount=outstanding,
        currency=i["currency"],
        idempotency_key=idempotency_key,
        customer_ref=await payment_customer(conn, str(i["tenant_id"])),
    )
    return await _record_attempt(
        conn, i, outstanding, result, idempotency_key=idempotency_key, now=now
    )


async def _record_attempt(
    conn: AsyncConnection,
    i: dict[str, Any],
    amount: Decimal,
    result: Any,
    *,
    idempotency_key: str,
    now: datetime,
) -> dict[str, Any]:
    """Write one payment attempt, emit its event, and (on a capture that clears every overdue invoice) return a
    past-due, restricted or suspended account to ACTIVE. Shared by a charge the platform made and by a payment the
    processor reported afterwards."""
    invoice_id = str(i["invoice_id"])
    aid = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO commercial.payment_attempt (attempt_id, tenant_id, invoice_id, idempotency_key, amount, "
            "currency, status, provider_ref, failure_code, attempted_at) VALUES (:a, :t, :i, :k, :m, :c, :s, :p, "
            ":f, :n)"
        ),
        {
            "a": aid,
            "t": i["tenant_id"],
            "i": uuid.UUID(invoice_id),
            "k": idempotency_key,
            "m": amount,
            "c": i["currency"],
            "s": result.status,
            "p": result.provider_ref,
            "f": result.failure_code,
            "n": now,
        },
    )
    await events.payment_recorded(
        conn,
        tenant_id=str(i["tenant_id"]),
        attempt_id=str(aid),
        invoice_id=invoice_id,
        status=result.status,
        amount=_d(amount),
        currency=i["currency"],
    )
    moved = None
    if result.status == "CAPTURED" and not await _unpaid_overdue(conn, now, exclude=invoice_id):
        st = await billing_state(conn)
        if st["state"] in ("PAST_DUE", "RESTRICTED", "SUSPENDED"):
            moved = await transition(
                conn, str(i["tenant_id"]), "ACTIVE", "PAID", actor=None, now=now
            )
    return {
        "attempt_id": str(aid),
        "status": result.status,
        "amount": _d(amount),
        "failure_code": result.failure_code,
        "replayed": False,
        "state_change": moved,
    }


async def attempt_statuses(conn: AsyncConnection, provider_ref: str) -> set[str]:
    """The statuses already recorded for one processor reference (a payment intent), under the current tenant."""
    rows = await conn.execute(
        text("SELECT status FROM commercial.payment_attempt WHERE provider_ref = :p"),
        {"p": provider_ref},
    )
    return {str(r[0]) for r in rows}


async def record_reported_payment(
    conn: AsyncConnection,
    invoice_id: str,
    *,
    status: str,
    provider_ref: str,
    failure_code: str | None,
    amount: Decimal,
    idempotency_key: str,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Record a payment outcome the processor reported after the fact (a webhook): a charge that finished later than
    the request that made it, or one made outside this platform's own call. Appended as its own attempt, keyed by the
    event, so a redelivery changes nothing. None when the invoice is not collectable any more (the caller flags it
    for a person: money moved for an invoice that is settled, void or not issued)."""
    from zid_platform.modules.commercial.application.adapters import PaymentResult

    now = now or datetime.now(UTC)
    i = await load_invoice(conn, invoice_id, lock=True)
    if status in await attempt_statuses(conn, provider_ref):
        return {
            "status": status,
            "replayed": True,
        }  # decided under the invoice lock: two callers cannot both record
    if i["status"] != "ISSUED":
        return None
    if status == "CAPTURED":
        outstanding = (
            Decimal(i["gross"]) - await _credited(conn, invoice_id) - await _paid(conn, invoice_id)
        )
        if outstanding <= 0:
            return None
    return await _record_attempt(
        conn,
        i,
        amount,
        PaymentResult(status, provider_ref=provider_ref, failure_code=failure_code),
        idempotency_key=idempotency_key,
        now=now,
    )


async def _unpaid_overdue(
    conn: AsyncConnection, now: datetime, *, exclude: str | None = None
) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT i.invoice_id, i.due_at, i.gross, "
                    "coalesce((SELECT sum(amount) FROM commercial.credit_note c WHERE c.invoice_id = i.invoice_id), 0) "
                    "+ coalesce((SELECT sum(amount) FROM commercial.payment_attempt p WHERE p.invoice_id = i.invoice_id "
                    "AND p.status = 'CAPTURED'), 0) AS settled "
                    "FROM commercial.invoice i WHERE i.status = 'ISSUED' AND i.due_at < :n ORDER BY i.due_at"
                ),
                {"n": now},
            )
        )
        .mappings()
        .all()
    )
    return [
        dict(r)
        for r in rows
        if Decimal(r["gross"]) - Decimal(r["settled"]) > 0 and str(r["invoice_id"]) != exclude
    ]


async def run_dunning(
    conn: AsyncConnection, tenant_id: str, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Move an account down the dunning ladder by the configured policy, one recorded step at a time.
    With no policy rows nothing moves: grace periods are a commercial decision, not a code default."""
    now = now or datetime.now(UTC)
    policy = [
        (int(r[0]), str(r[1]))
        for r in (
            await conn.execute(
                text("SELECT days_after_due, target_state FROM commercial.dunning_policy")
            )
        ).all()
    ]
    overdue = await _unpaid_overdue(conn, now)
    if not overdue or not policy:
        return []
    days = (now - overdue[0]["due_at"]).days
    st = await billing_state(conn)
    target = inv.dunning_target(st["state"], days, policy)
    moves: list[dict[str, Any]] = []
    current = st["state"]
    while target is not None and current != target:
        nxt = inv.next_dunning_step(current, target)
        reason = "PAYMENT_OVERDUE" if nxt == "PAST_DUE" else "DUNNING"
        moves.append(await transition(conn, tenant_id, nxt, reason, actor=None, now=now))
        current = nxt
    return moves


async def view(conn: AsyncConnection, invoice_id: str) -> dict[str, Any]:
    i = await load_invoice(conn, invoice_id)
    lines = await invoice_lines(conn, invoice_id)
    credited = await _credited(conn, invoice_id)
    paid = await _paid(conn, invoice_id)
    gross = Decimal(i["gross"])
    notes = (
        (
            await conn.execute(
                text(
                    "SELECT sequence_number, issuing_entity, amount, reason_code, issued_at FROM commercial.credit_note "
                    "WHERE invoice_id = :i ORDER BY issued_at"
                ),
                {"i": uuid.UUID(invoice_id)},
            )
        )
        .mappings()
        .all()
    )
    return {
        "invoice_id": str(i["invoice_id"]),
        "number": f"{i['issuing_entity']}-{int(i['sequence_number']):08d}"
        if i["sequence_number"]
        else None,
        "status": i["status"],
        "period": i["period"].strftime("%Y-%m"),
        "currency": i["currency"],
        "subtotal": _d(i["subtotal"]),
        "tax_total": _d(i["tax_total"]),
        "gross": _d(gross),
        "tax_status": i["tax_status"],
        "issued_at": i["issued_at"].isoformat() if i["issued_at"] else None,
        "due_at": i["due_at"].isoformat() if i["due_at"] else None,
        "document_digest": i["document_digest"],
        "credited": _d(credited),
        "paid": _d(paid),
        "outstanding": _d(max(ZERO, gross - credited - paid)) if i["status"] == "ISSUED" else None,
        "credit_notes": [
            {
                "number": f"{n['issuing_entity']}-{int(n['sequence_number']):08d}",
                "amount": _d(n["amount"]),
                "reason_code": n["reason_code"],
                "issued_at": n["issued_at"].isoformat(),
            }
            for n in notes
        ],
        "lines": [
            {
                "seq": ln["seq"],
                "kind": ln["kind"],
                "sku": ln["sku"],
                "meter": ln["meter_id"],
                "description": ln["description"],
                "net": _d(ln["net"]),
                "tax_code": ln["tax_code"],
                "tax": _d(ln["tax_amount"]),
                "gross": _d(ln["gross"]),
            }
            for ln in lines
        ],
    }


async def list_invoices(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT invoice_id FROM commercial.invoice WHERE status = 'ISSUED' "
                "ORDER BY issued_at DESC LIMIT 100"
            )
        )
    ).scalars()
    return [await view(conn, str(r)) for r in rows]


async def chain_entries(conn: AsyncConnection, entity: str) -> list[tuple[int, str, str]]:
    rows = (
        await conn.execute(
            text(
                "SELECT sequence_number, document_digest, chain_hash FROM commercial.invoice_chain "
                "WHERE issuing_entity = :e ORDER BY sequence_number"
            ),
            {"e": entity},
        )
    ).all()
    return [(int(r[0]), str(r[1]), str(r[2])) for r in rows]
