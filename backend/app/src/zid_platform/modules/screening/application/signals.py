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
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncConnection

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
)
from zid_platform.modules.screening.infrastructure import signal_store as store

log = logging.getLogger("zid.screening")
Notifier = Callable[[store.ReviewRow], Awaitable[None]]
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
    notify: Notifier | None,
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
            await notify(review)
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
    notify: Notifier | None = None,
) -> list[Raised]:
    """Count one sign-up, verification or claim attempt against the hashed device, contact and network keys it came with, and raise a
    review for every rule breached. `keys` holds digests (see `hash_identifier`), never raw values. The attempt that breaches is itself
    allowed to continue; what changes is that a person now has an item to review."""
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
    return resolved
