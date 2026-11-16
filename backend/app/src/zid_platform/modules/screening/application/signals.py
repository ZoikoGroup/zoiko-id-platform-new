"""Velocity and adverse-media signals and their review queue (CR-068; ZID-GAP-001 WP-14).

A signal only adds work for a person: it opens one review item (one per dedupe key, so a repeat adds nothing) with the rule, the
counts or the provider, and the time. It never blocks silently, never approves, and never clears. A notification that fails leaves
the item open and marks it `FAILED` so a sweep can retry; nothing is cleared by a failure. A person resolves an item with an outcome,
a rationale and a reference to the evidence they relied on; the signal and its detail stay as they were.

A PEP hit does not come through here: it is an ordinary list match, so it goes through `screening.open_case` and the existing review
(`screening.review`) and appeal mechanism, with the list version and matching rationale recorded in the signed evidence.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.screening.application.signal_events import (
    publish_signal_corrected,
    publish_signal_raised,
)
from zid_platform.modules.screening.domain.decision import ScreeningError
from zid_platform.modules.screening.domain.matching import Party
from zid_platform.modules.screening.domain.signals import (
    DEFAULT_RULES,
    Action,
    AdverseMediaResult,
    Breach,
    Dimension,
    MediaEffect,
    MediaOutcome,
    RiskTier,
    VelocityRule,
    apply_adverse_media,
    check_signal_resolution,
    evaluate_velocity,
    hash_identifier,
    network_prefix,
)
from zid_platform.modules.screening.infrastructure import signal_store as store

log = logging.getLogger("zid.screening")
Notifier = Callable[[store.ReviewRow], Awaitable[None]]
EVENT: Literal["event"] = (
    "event"  # the platform channel: a catalogued outbox event any consumer can act on
)
NotifyChannel = Notifier | Literal["event"] | None
MEDIA_TIMEOUT_S = 10.0  # PROPOSED


class AdverseMediaPort(Protocol):
    """A provider of adverse-media results. Provider-neutral: no real adapter exists yet (an owner decision)."""

    name: str

    async def search(self, *, subject_ref: str, party: Party) -> AdverseMediaResult: ...


@dataclass(frozen=True, slots=True)
class Raised:
    review: store.ReviewRow
    created: bool  # False when an open item for the same signal already existed


async def _audit(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    action: str,
    target: str,
    details: Mapping[str, Any],
    actor: str | None,
    reason: str | None,
    now: datetime,
) -> None:
    from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit

    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type="verification.screening.signal",
            actor_type="HUMAN" if actor else "SERVICE",
            action=action,
            target_type="screening_signal_review",
            target_ref=target,
            outcome="SUCCESS",
            evidence_class=EvidenceClass.E3,
            occurred_at=now,
            actor_principal_id=actor,
            reason_code=reason,
            source_service="screening",
            details=dict(details),  # codes, counts and hashes only
        ),
    )


async def _deliver(
    conn: AsyncConnection, tenant_id: str, review: store.ReviewRow, notify: NotifyChannel
) -> None:
    """One delivery attempt in its own savepoint, so a failure undoes only itself and cannot poison the caller's transaction."""
    if notify is None:
        return
    async with conn.begin_nested():
        if notify == EVENT:
            await publish_signal_raised(conn, tenant_id, review)
        else:
            await notify(review)


async def _raise(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    source: str,
    subject_ref: str,
    rule: str,
    detail: dict[str, Any],
    tier: RiskTier,
    dedupe_key: str,
    notify: NotifyChannel,
    now: datetime,
) -> Raised:
    review, created = await store.create_signal_and_review(
        conn, tenant_id, source=source, subject_ref=subject_ref, rule=rule, detail=detail,
        risk_tier=tier.value, dedupe_key=dedupe_key, now=now,
    )  # fmt: skip
    if not created:
        return Raised(review, False)
    await _audit(
        conn, tenant_id, action="raised", target=review.review_id,
        details={"source": source, "rule": rule, "risk_tier": tier.value, **detail}, actor=None, reason=rule, now=now,
    )  # fmt: skip
    if notify is not None:
        try:
            await _deliver(conn, tenant_id, review, notify)
            await store.set_notify_state(conn, tenant_id, review.review_id, "SENT")
        except Exception:  # fail safe: the item stays open and is marked, never dropped or cleared
            log.exception("notification for screening review %s failed", review.review_id)
            await store.set_notify_state(conn, tenant_id, review.review_id, "FAILED")
        review = (await store.get_review(conn, tenant_id, review.review_id)) or review
    return Raised(review, True)


# ---- velocity --------------------------------------------------------------------------------------------------------


async def record_attempt(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    subject_ref: str,
    action: Action,
    keys: Mapping[Dimension, str],
    now: datetime,
    rules: tuple[VelocityRule, ...] = DEFAULT_RULES,
    notify: NotifyChannel = None,
    shadow: bool = False,
) -> list[Raised]:
    """Count one sign-up, verification or claim attempt against the hashed device, contact and network keys it came with, and raise a
    review for every rule breached. `keys` holds digests (see `hash_identifier`), never raw values. The attempt that breaches is itself
    allowed to continue; what changes is that a person now has an item to review.

    In `shadow` mode the attempts are counted and each first crossing of a threshold is written to the audit trail as `shadow_breach`, but no review
    item is opened and nobody is notified: it is how the owners see what the proposed thresholds would have flagged on real traffic before they
    ask anyone to review it."""
    for dim, digest in keys.items():
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ScreeningError("IDENTIFIER_NOT_HASHED", dim.value)
        await store.record_event(conn, tenant_id, dim, digest, action, now)
    out: list[Raised] = []
    for dim, digest in keys.items():
        counts = {
            (dim, r.action, r.window_seconds): await store.count_events(conn, tenant_id, dim, digest, r.action, r.window, now)
            for r in rules
            if r.dimension is dim and r.action is action
        }  # fmt: skip
        breaches: list[Breach] = evaluate_velocity(counts, rules)
        for b in breaches:
            if shadow:
                if (
                    b.count == b.threshold
                ):  # the first crossing in a window, not every attempt after it
                    await _audit(
                        conn, tenant_id, action="shadow_breach", target=f"shadow:{b.rule}:{digest[:12]}",
                        details={**b.detail(), "key_hash": digest, "mode": "shadow"}, actor=None, reason=b.rule, now=now,
                    )  # fmt: skip
                continue
            out.append(
                await _raise(
                    conn,
                    tenant_id,
                    source="VELOCITY",
                    subject_ref=subject_ref,
                    rule=b.rule,
                    detail={**b.detail(), "key_hash": digest},
                    tier=b.tier,
                    dedupe_key=f"VELOCITY:{b.rule}:{digest}",
                    notify=notify,
                    now=now,
                )
            )
    return out


# ---- adverse media ---------------------------------------------------------------------------------------------------


async def check_adverse_media(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    subject_ref: str,
    party: Party,
    port: AdverseMediaPort,
    current_tier: RiskTier,
    now: datetime,
    timeout_s: float = MEDIA_TIMEOUT_S,
    notify: Notifier | None = None,
) -> tuple[MediaEffect, Raised | None]:
    """Ask the provider. Whatever it does, the answer can only add a review or raise the tier: a timeout, an exception or an answer that
    is not an `AdverseMediaResult` is recorded as a failed check and routes to review. The query text is never stored."""
    provider = getattr(port, "name", "unknown")
    try:
        result = await asyncio.wait_for(
            port.search(subject_ref=subject_ref, party=party), timeout_s
        )
        if not isinstance(result, AdverseMediaResult):
            raise TypeError("provider returned an unreadable result")
    except TimeoutError:
        result = AdverseMediaResult(
            provider, now, MediaOutcome.TIMEOUT, rationale="provider timed out"
        )
    except Exception as e:  # noqa: BLE001 - fail safe: an error is a review, never a pass
        log.warning("adverse media provider %s failed: %s", provider, type(e).__name__)
        result = AdverseMediaResult(
            provider, now, MediaOutcome.ERROR, rationale=f"provider error: {type(e).__name__}"
        )
    effect = apply_adverse_media(current_tier, result)
    detail = {
        "provider": result.provider,
        "queried_at": result.queried_at.isoformat(),
        "outcome": result.outcome.value,
        "hit_count": result.hit_count,
        "severity": result.severity.value,
        "rationale": result.rationale[:500],
        "reasons": list(effect.reasons),
    }
    if not effect.review_required:
        await _audit(
            conn, tenant_id, action="checked", target=subject_ref, details={"source": "ADVERSE_MEDIA", **detail},
            actor=None, reason=effect.reasons[0], now=now,
        )  # fmt: skip
        return effect, None
    raised = await _raise(
        conn, tenant_id, source="ADVERSE_MEDIA", subject_ref=subject_ref, rule=effect.reasons[0], detail=detail,
        tier=effect.tier, dedupe_key=f"ADVERSE_MEDIA:{subject_ref}:{effect.reasons[0]}", notify=notify, now=now,
    )  # fmt: skip
    return effect, raised


# ---- identity signals (CR-101; DTV-20/21/22) -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IdentityFinding:
    """One identity-signal result for a person to look at. Built by the verification module from its link graph; this module never
    sees a phone, an email or a device id, only an opaque subject, a rule code and counts."""

    rule: str
    subject_ref: str
    group_key: (
        str  # a stable digest of who is in the group, so the same group never opens a second item
    )
    tier: RiskTier
    counts: Mapping[str, int]


async def raise_identity_findings(
    conn: AsyncConnection,
    tenant_id: str,
    findings: list[IdentityFinding],
    *,
    now: datetime,
    notify: NotifyChannel = EVENT,
) -> list[Raised]:
    """Each finding opens one review item (a repeat of the same group adds nothing). A finding is a reason for a person to look: it
    blocks, approves and clears nothing, and the detail holds counts only."""
    out: list[Raised] = []
    for f in findings:
        detail = {k: int(v) for k, v in f.counts.items()}
        detail["recommendation_is_review_only"] = 1
        out.append(
            await _raise(
                conn, tenant_id, source="IDENTITY_SIGNAL", subject_ref=f.subject_ref, rule=f.rule, detail=detail,
                tier=f.tier, dedupe_key=f"IDENTITY_SIGNAL:{f.rule}:{f.group_key}", notify=notify, now=now,
            )
        )  # fmt: skip
    return out


# ---- review ----------------------------------------------------------------------------------------------------------


async def open_queue(conn: AsyncConnection, tenant_id: str) -> list[store.ReviewRow]:
    """Everything still waiting for a person, oldest first, including items whose notification failed."""
    return await store.list_open_reviews(conn, tenant_id)


async def challenge(
    conn: AsyncConnection,
    tenant_id: str,
    review_id: str,
    *,
    statement: str,
    evidence_ref: str,
    now: datetime,
) -> store.ReviewRow:
    """Someone disputes a signal. The item stays open (CHALLENGED) with the statement's hash and an evidence reference; only a person's
    resolution closes it."""
    row = await store.get_review(conn, tenant_id, review_id, lock=True)
    if row is None:
        raise ScreeningError("REVIEW_NOT_FOUND")
    if row.status == "RESOLVED":
        raise ScreeningError("SIGNAL_ALREADY_RESOLVED")
    if not 20 <= len(statement.strip()) <= 2000:
        raise ScreeningError(
            "STATEMENT_INVALID", "write at least a sentence, at most 2000 characters"
        )
    if not evidence_ref.strip():
        raise ScreeningError("EVIDENCE_REF_REQUIRED")
    digest = hashlib.sha256(statement.strip().encode()).hexdigest()
    await store.mark_challenged(
        conn, tenant_id, review_id, statement_hash=digest, evidence_ref=evidence_ref.strip()
    )
    await _audit(
        conn, tenant_id, action="challenged", target=review_id,
        details={"statement_hash": digest, "evidence_ref": evidence_ref.strip()}, actor=None, reason="CHALLENGED", now=now,
    )  # fmt: skip
    return (await store.get_review(conn, tenant_id, review_id)) or row


async def resolve(
    conn: AsyncConnection,
    tenant_id: str,
    review_id: str,
    *,
    reviewer_id: str,
    outcome: str,
    rationale: str,
    evidence_ref: str,
    now: datetime,
) -> store.ReviewRow:
    """A person's ruling. The outcome, rationale and evidence reference are recorded on the item; the signal row and its counts or
    provider detail are left exactly as raised."""
    row = await store.get_review(conn, tenant_id, review_id, lock=True)
    if row is None:
        raise ScreeningError("REVIEW_NOT_FOUND")
    refused = check_signal_resolution(outcome, rationale, evidence_ref, row.status)
    if refused is not None:
        raise ScreeningError(refused)
    await store.mark_resolved(
        conn, tenant_id, review_id, outcome=outcome, reviewer_id=reviewer_id,
        rationale=rationale.strip(), evidence_ref=evidence_ref.strip(), now=now,
    )  # fmt: skip
    await _audit(
        conn, tenant_id, action="resolved", target=review_id,
        details={"outcome": outcome, "evidence_ref": evidence_ref.strip(), "rule": row.rule}, actor=reviewer_id,
        reason=outcome, now=now,
    )  # fmt: skip
    resolved = await store.get_review(conn, tenant_id, review_id)
    assert resolved is not None
    if (
        outcome == "CLEARED_BY_REVIEWER"
    ):  # a cleared signal is a correction: whatever relied on it is told to decide again (AT-019)
        await publish_signal_corrected(conn, tenant_id, resolved)
    return resolved


# ---- wiring: one safe call from a sign-up, a verification or a claim ---------------------------------------------------------


async def note_attempt(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    subject_ref: str,
    action: Action,
    now: datetime,
    contact: str | None = None,
    network: str | None = None,
    device: str | None = None,
    pepper: bytes = b"",
    rules: tuple[VelocityRule, ...] = DEFAULT_RULES,
    notify: NotifyChannel = EVENT,
    mode: str = "review",
) -> list[Raised]:
    """Count one attempt against whatever keys it came with and raise a review for a breach. Raw values are hashed here and never stored:
    `contact` is an address or a stable person id, `network` a client address (kept as its /24 or /48), `device` an opaque id the client keeps.

    This is best effort and is never allowed to break the journey it watches: with no keys it does nothing, and any failure is logged and
    undone in a savepoint, so the sign-up, verification or claim goes on exactly as it would have. A breach never blocks; it opens an item
    for a person.

    `mode`: `off` does nothing, `shadow` counts and logs would-be breaches without opening items, `review` opens an item per breach."""
    if mode == "off":
        return []
    keys: dict[Dimension, str] = {}
    if contact and contact.strip():
        keys[Dimension.CONTACT] = hash_identifier(
            contact, tenant_id=tenant_id, kind="contact", pepper=pepper
        )
    prefix = network_prefix(network)
    if prefix is not None:
        keys[Dimension.NETWORK] = hash_identifier(
            prefix, tenant_id=tenant_id, kind="network", pepper=pepper
        )
    if device and device.strip():
        keys[Dimension.DEVICE] = hash_identifier(
            device[:128], tenant_id=tenant_id, kind="device", pepper=pepper
        )
    if not keys:
        return []
    try:
        async with conn.begin_nested():
            return await record_attempt(
                conn,
                tenant_id,
                subject_ref=subject_ref,
                action=action,
                keys=keys,
                now=now,
                rules=rules,
                notify=notify,
                shadow=(mode == "shadow"),
            )
    except Exception:
        log.exception("could not record a %s attempt for screening signals", action.value)
        return []


# ---- housekeeping (worker) --------------------------------------------------------------------------------------------------

RETAIN_EVENTS = timedelta(
    days=8
)  # longer than the longest rule window (24 hours), with room for a later rule change


async def prune_velocity(conn: AsyncConnection, now: datetime) -> int:
    """Drop attempt rows no window can still count. Signals and review items stay."""
    return await store.prune_events(conn, now - RETAIN_EVENTS)


async def retry_failed_notifications(
    conn: AsyncConnection, notify: NotifyChannel, now: datetime
) -> dict[str, int]:
    """Try again for items waiting for a person whose notification failed. A second failure leaves the item open and marked FAILED."""
    sent = failed = 0
    for tenant_id, review_id in await store.failed_notifications(conn):
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
        row = await store.get_review(conn, tenant_id, review_id)
        if row is None or row.status == "RESOLVED":
            continue
        try:
            await _deliver(conn, tenant_id, row, notify)
            await store.set_notify_state(conn, tenant_id, review_id, "SENT")
            sent += 1
        except Exception:
            log.exception("retry of the notification for screening review %s failed", review_id)
            failed += 1
    return {"sent": sent, "failed": failed}


def review_view(row: store.ReviewRow) -> dict[str, Any]:
    """What the review API returns for one item. Hashes and counts only: no raw address, phone or device id is ever stored."""
    return {
        "review_id": row.review_id,
        "signal_id": row.signal_id,
        "dedupe_key": row.dedupe_key,
        "status": row.status,
        "notify_state": row.notify_state,
        "outcome": row.outcome,
        "resolved_by": row.resolved_by,
        "rationale": row.rationale,
        "evidence_ref": row.evidence_ref,
        "source": row.source,
        "rule": row.rule,
        "detail": row.detail,
        "risk_tier": row.risk_tier,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
