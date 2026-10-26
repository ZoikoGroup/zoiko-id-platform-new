"""Sending one templated email, once (Communication Estate §03, §05).

    prepare   (short DB transaction)  switch check, event-level idempotency, suppression/consent/flood
                                      policy, render. Either a terminal report, or a PENDING row to send.
    deliver   (no DB transaction)     the SMTP conversation, with bounded retry on temporary failures.
    record    (short DB transaction)  the outcome, and a hard bounce becomes a suppression.

The network call never holds a transaction open. A crash between `deliver` and `record` leaves a PENDING row
that the next delivery of the same event may retry after a lease, so the worst case is one duplicate email,
never a lost security notice. Nothing in a row, a log line or a report carries an address, a token or a code.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from zid_context import RequestContext, new_id
from zid_db import tenant_transaction

from zid_platform.modules.communication.application.config import (
    LEASE_SECONDS,
    MARKETING_LISTS,
    MAX_ATTEMPTS,
    EmailConfig,
)
from zid_platform.modules.communication.application.contacts import Recipient
from zid_platform.modules.communication.domain.catalogue import MessageClass, Template
from zid_platform.modules.communication.domain.policy import Outcome, Suppression, decide
from zid_platform.modules.communication.domain.render import (
    RenderContext,
    Rendered,
    RenderError,
    render,
)
from zid_platform.modules.communication.infrastructure import store
from zid_platform.modules.communication.infrastructure.catalogue_file import load as load_catalogue
from zid_platform.modules.communication.infrastructure.smtp import EmailSender, SendResult
from zid_platform.modules.evidence.ports import current_runtime

log = logging.getLogger("zid.communication")
TERMINAL = frozenset({"SENT", "SUPPRESSED", "THROTTLED", "RENDER_FAILED", "DISABLED"})


@dataclass(frozen=True, slots=True)
class Report:
    outcome: str  # SENT | FAILED | SUPPRESSED | THROTTLED | RENDER_FAILED | DISABLED | DUPLICATE
    reason: str | None = None
    message_id: str | None = None


@dataclass(frozen=True, slots=True)
class Prepared:
    message_id: str
    template: Template
    recipient: Recipient
    rendered: Rendered
    base_attempts: int


# --------------------------------------------------------------------------- unsubscribe links (RFC 8058)


def _sig(recipient_hash: str, list_id: str) -> str:
    mac = current_runtime().mac_key.mac(f"unsubscribe-v1|{recipient_hash}|{list_id}".encode())
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()


def unsubscribe_url(base: str, recipient_hash: str, list_id: str) -> str:
    return f"{base}/email/unsubscribe?r={quote(recipient_hash, safe='')}&l={quote(list_id, safe='')}&s={_sig(recipient_hash, list_id)}"


def verify_unsubscribe(recipient_hash: str, list_id: str, sig: str) -> bool:
    return hmac.compare_digest(_sig(recipient_hash, list_id), sig)


# ----------------------------------------------------------------------------------------- phase one


def _context(cfg: EmailConfig, template: Template, recipient: Recipient) -> RenderContext:
    marketing = template.message_class is MessageClass.MARKETING
    list_id = MARKETING_LISTS.get(template.id, "")
    return RenderContext(
        sender_display_name=cfg.sender_display_name,
        links=cfg.links,
        support_url=cfg.support_url,
        locale=recipient.locale,
        timezone=recipient.timezone,
        recipient_display_name=recipient.display_name,
        marketing_basis="you asked to receive Zoiko iD product updates" if marketing else None,
        preferences_url=cfg.links.url_for("trusted_preferences_url", None)
        if marketing and cfg.links.routed("trusted_preferences_url")
        else None,
        unsubscribe_url=unsubscribe_url(
            cfg.unsubscribe_base or cfg.links.base_url, recipient.address_hash, list_id
        )
        if marketing
        else None,
        legal_entity=cfg.legal_entity if marketing else None,
        mailing_address=cfg.mailing_address if marketing else None,
    )


async def prepare(
    conn: AsyncConnection,
    cfg: EmailConfig,
    *,
    template_id: str,
    event_id: str,
    event_type: str,
    tenant_id: str,
    recipient: Recipient,
    merge: Mapping[str, object],
    correlation_id: str | None = None,
    now: datetime | None = None,
) -> Prepared | Report:
    now = now or datetime.now(UTC)
    template = load_catalogue().get(template_id)
    if not cfg.allows(template):
        return Report("DISABLED", "TEMPLATE_NOT_ENABLED")

    row, created = await store.reserve_message(
        conn,
        tenant_id=tenant_id,
        template_id=template.id,
        template_version=template.version,
        message_class=template.message_class.value,
        event_id=event_id,
        event_type=event_type,
        recipient_hash=recipient.address_hash,
        correlation_id=correlation_id,
    )
    if not created:
        stale = row.updated_at < now - timedelta(seconds=LEASE_SECONDS)
        retryable = (
            row.status == "FAILED" or (row.status == "PENDING" and stale)
        ) and row.attempts < MAX_ATTEMPTS
        if not retryable:
            return Report("DUPLICATE", row.status, row.message_id)

    list_id = MARKETING_LISTS.get(template.id, "")
    hour_ago = now - timedelta(hours=1)
    decision = decide(
        message_class=template.message_class,
        suppressions=await store.suppressions(conn, recipient.address_hash, list_id),
        consent=await store.consent_status(conn, recipient.address_hash, list_id)
        if list_id
        else None,
        template_sent_last_hour=await store.sent_last_hour(
            conn,
            tenant_id,
            recipient.address_hash,
            hour_ago,
            template.id,
            exclude_message_id=row.message_id,
        ),
        recipient_sent_last_hour=await store.sent_last_hour(
            conn,
            tenant_id,
            recipient.address_hash,
            hour_ago,
            None,
            exclude_message_id=row.message_id,
        ),
        limits=cfg.limits,
    )
    if not decision.send:
        status = "SUPPRESSED" if decision.outcome is Outcome.SUPPRESSED else "THROTTLED"
        await store.finish_message(
            conn, row.message_id, status=status, reason=decision.reason, provider_ref=None, now=now
        )
        return Report(status, decision.reason, row.message_id)

    try:
        rendered = render(template, merge, _context(cfg, template, recipient))
    except RenderError as e:
        await store.finish_message(
            conn, row.message_id, status="RENDER_FAILED", reason=e.code, provider_ref=None, now=now
        )
        log.warning(
            "email render refused template=%s code=%s field=%s", template.id, e.code, e.field
        )
        return Report("RENDER_FAILED", e.code, row.message_id)

    await store.begin_attempt(conn, row.message_id, now)
    return Prepared(row.message_id, template, recipient, rendered, row.attempts)


# ----------------------------------------------------------------------------------------- phase two


async def deliver(
    sender: EmailSender, cfg: EmailConfig, prepared: Prepared
) -> tuple[SendResult, int]:
    """Send with bounded retry on temporary failures. Returns the final result and the attempts made."""
    result = SendResult("TEMPORARY", "NOT_ATTEMPTED")
    made = 0
    for delay in cfg.retry_delays_s:
        if delay:
            await asyncio.sleep(delay)
        made += 1
        result = await sender.send(
            to=prepared.recipient.address,
            rendered=prepared.rendered,
            message_id=prepared.message_id,
        )
        if result.kind != "TEMPORARY":
            break
    return result, made


# --------------------------------------------------------------------------------------- phase three


async def record(
    conn: AsyncConnection,
    prepared: Prepared,
    result: SendResult,
    attempts_made: int,
    now: datetime | None = None,
) -> Report:
    now = now or datetime.now(UTC)
    mid = prepared.message_id
    if result.sent:
        await store.finish_message(
            conn, mid, status="SENT", reason=None, provider_ref=result.provider_ref, now=now
        )
        await store.set_attempts(conn, mid, prepared.base_attempts + attempts_made)
        return Report("SENT", None, mid)
    await store.finish_message(
        conn, mid, status="FAILED", reason=result.code or result.kind, provider_ref=None, now=now
    )
    await store.set_attempts(conn, mid, prepared.base_attempts + attempts_made)
    if result.kind == "RECIPIENT_REJECTED":
        # a permanent refusal of this address is a hard bounce: stop mailing it, in every tenant
        await store.add_suppression(
            conn, prepared.recipient.address_hash, Suppression.BOUNCE_HARD, source="smtp"
        )
    return Report("FAILED", result.code or result.kind, mid)


# ------------------------------------------------------------------------------------------ one call


async def send_email(
    engine: AsyncEngine,
    cfg: EmailConfig,
    sender: EmailSender,
    *,
    cell_id: str,
    template_id: str,
    event_id: str,
    event_type: str,
    tenant_id: str,
    recipient: Recipient,
    merge: Mapping[str, object],
    correlation_id: str | None = None,
) -> Report:
    """prepare, deliver and record, each in its own place; never raises for a delivery problem."""
    ctx = RequestContext(
        new_id("req"),
        correlation_id or new_id("cor"),
        "communication",
        cell_id,
        tenant_id=tenant_id,
    )
    async with tenant_transaction(engine, ctx) as conn:
        prepared = await prepare(
            conn,
            cfg,
            template_id=template_id,
            event_id=event_id,
            event_type=event_type,
            tenant_id=tenant_id,
            recipient=recipient,
            merge=merge,
            correlation_id=correlation_id,
        )
    if isinstance(prepared, Report):
        return prepared
    result, made = await deliver(sender, cfg, prepared)
    async with tenant_transaction(engine, ctx) as conn:
        report = await record(conn, prepared, result, made)
    log.info(
        "email %s template=%s message=%s reason=%s",
        report.outcome,
        prepared.template.id,
        report.message_id,
        report.reason,
    )
    return report


def event_key(event: Any) -> str:
    """The idempotency key of an event-driven send: stable across redeliveries of the same event."""
    return hashlib.sha256(str(event.id).encode()).hexdigest()[:40]
