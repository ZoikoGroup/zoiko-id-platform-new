"""Source events into the analytics fact store (ZID-CPS-WF-02 section 04: event producers, idempotent ingestion, append-only facts, freshness).

Called by the ingest consumer inside the event's own tenant transaction and the inbox (`zid_outbox.consume`), so a replayed event is one fact
(Q04). Only allow-listed event types are kept, and of each only allow-listed fields: ids, codes, times and counts, never a name, a document
field, media or an AML payload (section 04.1). An event that cannot be placed (no tenant, no version, no time) is counted as rejected, never
guessed at. An event older than the newest already seen from its source is counted late; it still lands, because the metrics are built from
event times, not arrival order (Q05)."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import CloudEvent

WEBHOOK = ("delivery_id", "attempt", "status_code", "final", "delay_ms", "event_type")
ALLOWED: dict[str, tuple[str, ...]] = {
    "verification.session.created": ("expires_at", "purpose_code"),
    "verification.session.notice_presented": (),
    "verification.session.notice_acknowledged": ("locale", "lawful_basis"),
    "verification.session.method_selected": ("method", "policy_version"),
    "verification.session.capture_started": ("step", "method"),
    "verification.session.capture_attempt_completed": ("attempt_no", "step", "result"),
    "verification.session.media_accepted": ("source_attestation_result",),
    "verification.session.submitted": (),
    "verification.session.review_required": ("state", "decision_version", "due_at", "decided_by"),
    "verification.session.finalized": (
        "state",
        "outcome",
        "decision_version",
        "decided_by",
        "finding_code",
        "policy_version",
    ),
    "verification.session.canceled": ("by",),
    "verification.idv.provider_check_finished": ("provider", "outcome", "latency_ms"),
    "webhook.delivery.succeeded": WEBHOOK,
    "webhook.delivery.failed": WEBHOOK,
    "webhook.delivery.exhausted": WEBHOOK,
}
DOMAINS = ("verification", "webhook")  # the source domains the consumer subscribes to
_TYPE = re.compile(r"^com\.zoiko\.id\.(?P<name>[a-z_.]+)\.v(?P<v>\d+)$")


def short_name(cloudevents_type: str) -> str | None:
    """`com.zoiko.id.verification.session.created.v1` -> `verification.session.created`; None for an unversioned or foreign type."""
    m = _TYPE.match(cloudevents_type)
    return m.group("name") if m else None


def wanted(cloudevents_type: str) -> bool:
    return short_name(cloudevents_type) in ALLOWED


async def ingest(conn: AsyncConnection, event: CloudEvent) -> bool:
    """True when a new fact was written. Runs inside the event's tenant transaction."""
    name = short_name(event.type)
    if name not in ALLOWED:
        return False
    source = name.split(".")[0]
    data = event.data or {}
    try:
        tenant = uuid.UUID(event.tenantid)
        occurred = event.time
        if occurred is None:
            raise ValueError("no time")
    except (TypeError, ValueError):
        await _count(conn, event.tenantid, source, event.time, rejected=True)
        return False
    attrs = {k: data[k] for k in ALLOWED[name] if data.get(k) is not None}
    if name.startswith("webhook."):
        attrs["subscribed_type"] = attrs.pop("event_type", None)
    case = data.get("case_id")
    written = (
        await conn.execute(
            text(
                "INSERT INTO metering.va_fact (event_id, tenant_id, event_type, source, session_ref, case_id, environment, market, occurred_at, attrs) "
                "VALUES (:id, :t, :ty, :src, :ref, :case, :env, :mk, :at, CAST(:attrs AS jsonb)) ON CONFLICT (event_id) DO NOTHING"
            ),
            {
                "id": uuid.UUID(event.id), "t": tenant, "ty": name, "src": source, "ref": data.get("session_ref"),
                "case": uuid.UUID(str(case)) if case else None, "env": data.get("environment"), "mk": data.get("market"),
                "at": occurred, "attrs": json.dumps(attrs, separators=(",", ":"), default=str),
            },
        )
    ).rowcount  # fmt: skip
    if written:
        await _count(conn, str(tenant), source, occurred)
    return bool(written)


async def _count(
    conn: AsyncConnection, tenant: str, source: str, occurred: Any, *, rejected: bool = False
) -> None:
    try:
        t = uuid.UUID(str(tenant))
    except ValueError:
        return  # no tenant to attribute it to: nothing can be counted for anyone
    await conn.execute(
        text("""
            INSERT INTO metering.va_watermark (tenant_id, source, last_occurred_at, last_ingested_at, accepted, late, rejected)
            VALUES (:t, :s, coalesce(:at, now()), now(), :a, 0, :r)
            ON CONFLICT (tenant_id, source) DO UPDATE SET
                late = metering.va_watermark.late + CASE WHEN :a = 1 AND :at < metering.va_watermark.last_occurred_at THEN 1 ELSE 0 END,
                last_occurred_at = greatest(metering.va_watermark.last_occurred_at, coalesce(:at, metering.va_watermark.last_occurred_at)),
                last_ingested_at = now(),
                accepted = metering.va_watermark.accepted + :a,
                rejected = metering.va_watermark.rejected + :r
        """),
        {"t": t, "s": source, "at": occurred, "a": 0 if rejected else 1, "r": 1 if rejected else 0},
    )


async def purge_expired(conn: AsyncConnection, *, before: Any) -> int:
    """The retention sweep (section 09: no indefinite analytics retention): facts older than the period go, except those a correction
    refers to (the journal must stay explainable). Runs in the tenant's transaction; the trigger allows deletes only under this flag."""
    await conn.execute(text("SELECT set_config('zid.va_purge', 'on', true)"))
    gone = (
        await conn.execute(
            text(
                "DELETE FROM metering.va_fact f WHERE occurred_at < :b AND NOT EXISTS (SELECT 1 FROM metering.va_correction c WHERE c.event_id = f.event_id)"
            ),
            {"b": before},
        )
    ).rowcount
    await conn.execute(text("SELECT set_config('zid.va_purge', 'off', true)"))
    return int(gone or 0)
