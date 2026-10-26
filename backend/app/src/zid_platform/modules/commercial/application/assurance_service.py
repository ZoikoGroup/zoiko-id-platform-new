"""Change log, revenue-assurance detectors, month-end close, journals, audit export and KPIs (CR-030).

The detectors that need a tenant's RLS rows run inside that tenant's scope; the ones over operator-plane
tables (orders, vendor disputes) run across the platform. Every finding is an exception with an owner.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_crypto import CryptoError, Signer, sign_jws, verify_jws

from zid_platform.modules.commercial.application import (
    invoicing_service,
    rating_service,
    recorder,
)
from zid_platform.modules.commercial.domain import assurance as a
from zid_platform.modules.commercial.infrastructure import billable_store


def _d(v: Any) -> str | None:
    return None if v is None else format(Decimal(v).normalize(), "f")


# ------------------------------------------------------------------------------- exceptions


async def raise_exception(
    conn: AsyncConnection, kind: str, ref: str, *, tenant_id: str | None, detail: Mapping[str, Any]
) -> bool:
    if kind not in a.EXCEPTION_KINDS:
        raise a.AssuranceError("EXCEPTION_KIND_UNKNOWN", kind)
    r = await conn.execute(
        text(
            "INSERT INTO commercial.assurance_exception (exception_id, kind, ref, tenant_id, detail) "
            "VALUES (:i, :k, :r, :t, CAST(:d AS jsonb)) ON CONFLICT (kind, ref) WHERE status = 'OPEN' DO NOTHING"
        ),
        {
            "i": uuid.uuid4(),
            "k": kind,
            "r": ref,
            "t": uuid.UUID(tenant_id) if tenant_id else None,
            "d": json.dumps(dict(detail), default=str),
        },
    )
    return bool(r.rowcount)


async def detect_tenant(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    sources: Mapping[str, Any],
    month: date,
) -> list[dict[str, str]]:
    """The detectors over one tenant's rows (run in its RLS scope)."""
    found: list[dict[str, str]] = []

    async def hit(kind: str, ref: str, detail: Mapping[str, Any]) -> None:
        if await raise_exception(conn, kind, ref, tenant_id=tenant_id, detail=detail):
            found.append({"kind": kind, "ref": ref})

    # an entitlement no signed order backs (pilots and support changes are reviewed, not silently accepted)
    for r in (
        await conn.execute(
            text(
                "SELECT entitlement_id, plan_code FROM commercial.entitlement "
                "WHERE state IN ('ACTIVE', 'SUSPENDED') AND source_order_id IS NULL"
            )
        )
    ).all():
        await hit("ENTITLEMENT_WITHOUT_ORDER", str(r[0]), {"plan": r[1]})
    # an order-backed entitlement whose price book is not the order's
    for r in (
        await conn.execute(
            text(
                "SELECT e.entitlement_id FROM commercial.entitlement e JOIN commercial.sales_order o "
                "ON o.order_id = e.source_order_id WHERE e.state IN ('ACTIVE', 'SUSPENDED') "
                "AND e.price_book_id IS DISTINCT FROM o.price_book_id"
            )
        )
    ).all():
        await hit("ENTITLEMENT_BOOK_DRIFT", str(r[0]), {})
    # meter loss: an authoritative row with no billable event (a dry run of reconciliation)
    start, end = rating_service.bounds(month)
    for meter, (fetch, make_key, _pclass) in sources.items():
        keys = [make_key(rid) for rid, _ in await fetch(conn, start, end)]
        if not keys:
            continue
        have = set(
            (
                await conn.execute(
                    text(
                        "SELECT deduplication_key FROM commercial.billable_event "
                        "WHERE deduplication_key = ANY(:k)"
                    ),
                    {"k": keys},
                )
            ).scalars()
        )
        missing = [k for k in keys if k not in have]
        if missing:
            await hit("METER_LOSS", f"{tenant_id}:{meter}:{month:%Y%m}", {"missing": len(missing)})
    # revenue leakage: a frozen month never rated, a computed rating never invoiced
    frozen = await conn.execute(text("SELECT DISTINCT window_month FROM commercial.meter_window"))
    for (m,) in frozen.all():
        run = (
            await conn.execute(
                text(
                    "SELECT run_id FROM commercial.rating_run WHERE period = :p AND state = 'COMPUTED'"
                ),
                {"p": m},
            )
        ).scalar()
        if run is None:
            await hit("UNRATED_MONTH", f"{tenant_id}:{m:%Y%m}", {})
            continue
        invoiced = (
            await conn.execute(
                text(
                    "SELECT 1 FROM commercial.invoice WHERE rating_run_id = :r AND status = 'ISSUED'"
                ),
                {"r": run},
            )
        ).first()
        if invoiced is None:
            await hit("UNINVOICED_RUN", str(run), {"period": f"{m:%Y-%m}"})
    return found


async def detect_platform(conn: AsyncConnection) -> list[dict[str, str]]:
    """The detectors over operator-plane tables (orders, vendor disputes)."""
    found: list[dict[str, str]] = []
    for r in (
        await conn.execute(
            text(
                "SELECT o.order_id, l.sku_id, l.net_price, f.floor_price FROM commercial.sales_order o "
                "JOIN commercial.sales_order_line l ON l.order_id = o.order_id "
                "JOIN commercial.quote q ON q.quote_id = o.quote_id "
                "JOIN commercial.price_floor f ON f.sku_id = l.sku_id AND f.region = q.region "
                "WHERE l.net_price IS NOT NULL AND l.net_price < f.floor_price "
                "AND NOT ('CFO' = ANY(q.approvers_required) AND q.status = 'ACCEPTED')"
            )
        )
    ).all():
        if await raise_exception(
            conn,
            "ORDER_BELOW_FLOOR",
            f"{r[0]}:{r[1]}",
            tenant_id=None,
            detail={"net": _d(r[2]), "floor": _d(r[3])},
        ):
            found.append({"kind": "ORDER_BELOW_FLOOR", "ref": f"{r[0]}:{r[1]}"})
    for r in (
        await conn.execute(
            text(
                "SELECT vendor_id, invoice_ref, count(*) FROM commercial.vendor_dispute WHERE status = 'OPEN' GROUP BY 1, 2"
            )
        )
    ).all():
        ref = f"{r[0]}:{r[1]}"
        if await raise_exception(
            conn, "SUPPLIER_DISPUTE_OPEN", ref, tenant_id=None, detail={"open": int(r[2])}
        ):
            found.append({"kind": "SUPPLIER_DISPUTE_OPEN", "ref": ref})
    return found


async def exceptions(conn: AsyncConnection, *, status: str = "OPEN") -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT exception_id, kind, ref, tenant_id, detail, status, raised_at FROM commercial.assurance_exception "
                "WHERE status = :s ORDER BY raised_at"
            ),
            {"s": status},
        )
    ).all()
    return [
        {
            "exception_id": str(r[0]),
            "kind": r[1],
            "ref": r[2],
            "tenant_id": str(r[3]) if r[3] else None,
            "detail": r[4],
            "status": r[5],
            "raised_at": r[6].isoformat(),
        }
        for r in rows
    ]


async def resolve(conn: AsyncConnection, exception_id: str, *, owner: str, resolution: str) -> bool:
    r = await conn.execute(
        text(
            "UPDATE commercial.assurance_exception SET status = 'RESOLVED', owner = :o, resolution = :r, "
            "resolved_at = now() WHERE exception_id = :i AND status = 'OPEN'"
        ),
        {"o": uuid.UUID(owner), "r": resolution, "i": uuid.UUID(exception_id)},
    )
    return bool(r.rowcount)


# ------------------------------------------------------------------------------- close


async def close_month(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    month: date,
    connections: Mapping[str, int],
    issuing_entity: str,
    actor: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Freeze, rate and draft. Each step is recorded and skipped when already done, so a close resumes where it
    stopped. Issuing stays a separate second-person step (CR-027)."""
    now = now or datetime.now(UTC)
    await conn.execute(
        text(
            "INSERT INTO commercial.close_run (tenant_id, period) VALUES (:t, :p) ON CONFLICT DO NOTHING"
        ),
        {"t": uuid.UUID(tenant_id), "p": month},
    )
    state = (
        (
            await conn.execute(
                text("SELECT * FROM commercial.close_run WHERE period = :p FOR UPDATE"),
                {"p": month},
            )
        )
        .mappings()
        .one()
    )
    steps: list[str] = []
    if state["frozen_at"] is None:
        await recorder.freeze_month(conn, tenant_id, month)
        await conn.execute(
            text(
                "UPDATE commercial.close_run SET frozen_at = :n, updated_at = :n WHERE period = :p"
            ),
            {"n": now, "p": month},
        )
        steps.append("FROZEN")
    if state["rated_at"] is None:
        run = await rating_service.run(
            conn, tenant_id=tenant_id, month=month, connections=dict(connections), actor=actor
        )
        await conn.execute(
            text(
                "UPDATE commercial.close_run SET rated_at = :n, rating_run_id = :r, updated_at = :n WHERE period = :p"
            ),
            {"n": now, "r": uuid.UUID(run["run_id"]), "p": month},
        )
        steps.append("RATED")
    if state["drafted_at"] is None:
        inv = await invoicing_service.create_draft(
            conn, tenant_id=tenant_id, month=month, issuing_entity=issuing_entity, actor=actor
        )
        await conn.execute(
            text(
                "UPDATE commercial.close_run SET drafted_at = :n, invoice_id = :i, updated_at = :n WHERE period = :p"
            ),
            {"n": now, "i": uuid.UUID(inv["invoice_id"]), "p": month},
        )
        steps.append("DRAFTED")
    final = (
        (
            await conn.execute(
                text("SELECT * FROM commercial.close_run WHERE period = :p"), {"p": month}
            )
        )
        .mappings()
        .one()
    )
    return {
        "month": f"{month:%Y-%m}",
        "done_now": steps,
        "invoice_id": str(final["invoice_id"]) if final["invoice_id"] else None,
        "rating_run_id": str(final["rating_run_id"]) if final["rating_run_id"] else None,
        "complete": all(final[k] for k in ("frozen_at", "rated_at", "drafted_at")),
    }


# ------------------------------------------------------------------------------- journals and audit


async def journal(conn: AsyncConnection, start: datetime, end: datetime) -> dict[str, Any]:
    """Journal lines derived from this tenant's issued invoices, credit notes and captured payments."""
    out: list[a.JournalLine] = []
    invoices = (
        await conn.execute(
            text(
                "SELECT invoice_id, issuing_entity, sequence_number FROM commercial.invoice "
                "WHERE status = 'ISSUED' AND issued_at >= :s AND issued_at < :e ORDER BY issued_at"
            ),
            {"s": start, "e": end},
        )
    ).all()
    for iid, entity, seq in invoices:
        lines = (
            await conn.execute(
                text(
                    "SELECT kind, net, tax_amount FROM commercial.invoice_line WHERE invoice_id = :i"
                ),
                {"i": iid},
            )
        ).all()
        out += a.invoice_journal(
            f"{entity}-{int(seq):08d}", [(k, Decimal(n), Decimal(t)) for k, n, t in lines]
        )
    for entity, seq, amount in (
        await conn.execute(
            text(
                "SELECT issuing_entity, sequence_number, amount FROM commercial.credit_note "
                "WHERE issued_at >= :s AND issued_at < :e ORDER BY issued_at"
            ),
            {"s": start, "e": end},
        )
    ).all():
        out += a.credit_note_journal(f"{entity}-{int(seq):08d}", Decimal(amount))
    for ref, amount in (
        await conn.execute(
            text(
                "SELECT provider_ref, amount FROM commercial.payment_attempt WHERE status = 'CAPTURED' "
                "AND attempted_at >= :s AND attempted_at < :e ORDER BY attempted_at"
            ),
            {"s": start, "e": end},
        )
    ).all():
        out += a.payment_journal(str(ref), Decimal(amount))
    return {
        "balanced": a.balanced(out),
        "lines": [
            {
                "account": e.account,
                "debit": _d(e.debit),
                "credit": _d(e.credit),
                "reference": e.reference,
            }
            for e in out
        ],
    }


async def audit_pack(conn: AsyncConnection, tenant_id: str, month: date) -> dict[str, Any]:
    """One tenant-month's evidence: frozen window hashes, rating hashes, invoice digests and the change log,
    with a digest over the whole bundle (§11 month-end audit pack)."""
    windows = [
        {"meter": w["meter_id"], "events": int(w["event_count"]), "hash_total": w["hash_total"]}
        for w in await billable_store.windows(conn, month)
    ]
    run = await rating_service.latest(conn, month)
    invoices = [
        {
            "number": f"{r[0]}-{int(r[1]):08d}",
            "document_digest": r[2],
            "chain_hash": r[3],
            "gross": _d(r[4]),
        }
        for r in (
            await conn.execute(
                text(
                    "SELECT issuing_entity, sequence_number, document_digest, chain_hash, gross FROM commercial.invoice "
                    "WHERE period = :p AND status = 'ISSUED' ORDER BY sequence_number"
                ),
                {"p": month},
            )
        ).all()
    ]
    changes = [
        {"seq": int(r[0]), "kind": r[1], "ref": r[2], "chain_hash": r[3], "at": r[4].isoformat()}
        for r in (
            await conn.execute(
                text(
                    "SELECT seq, kind, ref, chain_hash, at FROM commercial.change_log WHERE tenant_id = :t ORDER BY seq"
                ),
                {"t": uuid.UUID(tenant_id)},
            )
        ).all()
    ]
    bundle = {
        "tenant": invoicing_service.tenant_alias(tenant_id),
        "month": f"{month:%Y-%m}",
        "meter_windows": windows,
        "rating": {
            "inputs_hash": run["inputs_hash"],
            "outputs_hash": run["outputs_hash"],
            "subtotal": run["subtotal"],
        }
        if run
        else None,
        "invoices": invoices,
        "change_log": changes,
    }
    return {**bundle, "bundle_digest": a.digest(bundle)}


async def kpis(conn: AsyncConnection, now: datetime | None = None) -> dict[str, Any]:
    """MRR and ARR from activated orders on their own price books, intercompany excluded."""
    rows = (
        await conn.execute(
            text(
                "SELECT o.plan_code, l.unit_rate, a.kind FROM commercial.sales_order o "
                "JOIN commercial.account a ON a.account_id = o.account_id "
                "JOIN commercial.price_book_line l ON l.price_book_id = o.price_book_id AND l.plan_code = o.plan_code "
                "AND l.sku_id = 'ZID-BASE' WHERE o.status = 'ACTIVATED' AND NOT o.pilot"
            )
        )
    ).all()
    counts: dict[tuple[str, Decimal, bool], int] = {}
    for plan, fee, kind in rows:
        key = (str(plan), Decimal(fee), kind == "INTERNAL")
        counts[key] = counts.get(key, 0) + 1
    mrr = Decimal(0)
    for (plan, fee, internal), n in counts.items():
        mrr += a.run_rate([(plan, n, internal)], {plan: fee})["mrr"]
    return {
        "mrr": _d(mrr),
        "arr": _d(mrr * 12),
        "definitions": a.KPI_DEFINITIONS,
        "as_of": (now or datetime.now(UTC)).isoformat(),
        "basis": "activated, non-pilot orders at their contracted price book",
    }


AUDIT_PACK_TYP = "zid-commercial-audit-pack+jws"
_PACK_FIELDS = ("signature",)


class AuditPackError(ValueError):
    """A signed audit pack failed verification; the message starts with a stable reason code."""


def sign_audit_pack(
    signer: Signer, pack: Mapping[str, Any], *, issuer: str, now: datetime | None = None
) -> dict[str, Any]:
    """Sign a tenant-month audit pack with the evidence signing key (DEC-F5).

    The signed payload is the pack itself (so the bundle digest it carries is covered) plus who signed it
    and when. Anyone holding the evidence public keys can verify it offline with `verify_audit_pack`;
    the signature is never trusted from the key stored beside it."""
    at = now or datetime.now(UTC)
    claims = {
        **{k: v for k, v in pack.items() if k not in _PACK_FIELDS},
        "iss": issuer,
        "iat": int(at.timestamp()),
    }
    token = sign_jws(signer, claims, typ=AUDIT_PACK_TYP)
    return {
        **pack,
        "signature": {"alg": signer.alg, "kid": signer.kid, "typ": AUDIT_PACK_TYP, "jws": token},
    }


def verify_audit_pack(signed: Mapping[str, Any], *, keys: list[dict[str, Any]]) -> dict[str, Any]:
    """Verify a signed audit pack against trusted public keys. Raises `AuditPackError` with a reason when the
    signature is bad, the key is unknown, or the pack differs from what was signed."""
    sig = signed.get("signature")
    if not isinstance(sig, Mapping) or not isinstance(sig.get("jws"), str):
        raise AuditPackError("AUDIT_PACK_UNSIGNED")
    try:
        _, payload = verify_jws(sig["jws"], keys=keys, typ=AUDIT_PACK_TYP)
    except CryptoError as e:
        raise AuditPackError(f"AUDIT_PACK_SIGNATURE_INVALID: {e}") from e
    body = {k: v for k, v in signed.items() if k not in _PACK_FIELDS}
    signed_body = {k: v for k, v in payload.items() if k not in ("iss", "iat")}
    if json.loads(json.dumps(body, default=str)) != json.loads(
        json.dumps(signed_body, default=str)
    ):
        raise AuditPackError("AUDIT_PACK_ALTERED")
    bundle = {k: v for k, v in body.items() if k != "bundle_digest"}
    if a.digest(bundle) != body.get("bundle_digest"):
        raise AuditPackError("AUDIT_PACK_DIGEST_MISMATCH")
    return payload
