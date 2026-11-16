"""Credential lifecycle: source changes, ordering, expiry and recompute (ZID-GAP-001 v2.0 GID-058, GID-060). Pure domain.

A source tells us about a credential over time, and tells us out of order. Rules:
- the NEWEST valid observation wins, by the source's own sequence number when both carry one, otherwise by observation time;
  a late-arriving older event is kept in the history and changes nothing (GLB-26);
- the same event id twice is the same event (idempotent);
- a status change recomputes the dimension it feeds, and a dimension that is no longer verified downgrades the Zoikorum tier
  at once (GLB-04, GLB-10, GLB-19, GLB-36);
- expiry is looked ahead of, not found out about: `due_for_recheck` names credentials that expire inside the lead time, and a
  credential whose own check is older than its policy's freshness is stale, not good;
- a source that cannot be reached leaves the previous state in place and marks it unconfirmed after a grace period; it never
  turns a good credential bad, or a bad one good.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from zid_platform.modules.credential.domain.claims import CredentialStatus
from zid_platform.modules.credential.domain.eligibility import Dimension, DimStatus

CLAIM_DIMENSION = {
    "ACADEMIC_AWARD": Dimension.CREDENTIAL,
    "INDUSTRY_CERTIFICATE": Dimension.CREDENTIAL,
    "CPD_RECORD": Dimension.CREDENTIAL,
    "EMPLOYMENT_ASSERTION": Dimension.CREDENTIAL,
    "PROFESSIONAL_LICENSE": Dimension.ELIGIBILITY,
    "INSURANCE_ASSERTION": Dimension.INSURANCE,
    "DISCIPLINARY_FINDING": Dimension.RESTRICTIONS,
}


@dataclass(frozen=True, slots=True)
class SourceEvent:
    event_id: str
    claim_ref: str
    status: CredentialStatus
    observed_at: datetime
    sequence: int | None = None


@dataclass(frozen=True, slots=True)
class LifecycleState:
    claim_ref: str
    status: CredentialStatus = CredentialStatus.UNKNOWN
    sequence: int | None = None
    observed_at: datetime | None = None
    seen: frozenset[str] = frozenset()
    history: tuple[tuple[str, str, bool], ...] = field(
        default_factory=tuple
    )  # (event_id, status, applied)


def _newer(event: SourceEvent, state: LifecycleState) -> bool:
    if state.observed_at is None and state.sequence is None:
        return True
    if event.sequence is not None and state.sequence is not None:
        return event.sequence > state.sequence
    return state.observed_at is None or event.observed_at > state.observed_at


@dataclass(frozen=True, slots=True)
class Applied:
    state: LifecycleState
    changed: bool  # the effective status changed: emit the event, recompute, invalidate consumers
    applied: bool  # the event was the newest and was taken


def apply_event(state: LifecycleState, event: SourceEvent) -> Applied:
    if event.claim_ref != state.claim_ref:
        raise ValueError("event is for another claim")
    if event.event_id in state.seen:
        return Applied(state, False, False)  # replay: nothing happens twice
    take = _newer(event, state)
    nxt = LifecycleState(
        claim_ref=state.claim_ref,
        status=event.status if take else state.status,
        sequence=(event.sequence if take else state.sequence),
        observed_at=(event.observed_at if take else state.observed_at),
        seen=state.seen | {event.event_id},
        history=(*state.history, (event.event_id, event.status.value, take)),
    )
    return Applied(nxt, take and event.status is not state.status, take)


def dimension_status(claim_type: str, status: CredentialStatus) -> DimStatus:
    """What a credential's status means for the dimension it feeds."""
    if status is CredentialStatus.ACTIVE:
        return DimStatus.VERIFIED
    if status is CredentialStatus.EXPIRED:
        return DimStatus.EXPIRED
    return DimStatus.NOT_VERIFIED


@dataclass(frozen=True, slots=True)
class Tracked:
    claim_ref: str
    claim_type: str
    expires_at: datetime | None
    checked_at: datetime


def due_for_recheck(
    items: Sequence[Tracked], *, now: datetime, lead: timedelta, freshness: timedelta
) -> list[tuple[str, str]]:
    """(claim_ref, reason) for credentials to re-ask the source about: expiring inside the lead time, already expired, or
    last checked longer ago than the policy allows. Earliest first."""
    out: list[tuple[datetime, str, str]] = []
    for t in items:
        if t.expires_at is not None and now >= t.expires_at:
            out.append((t.expires_at, t.claim_ref, "EXPIRED"))
        elif t.expires_at is not None and t.expires_at - now <= lead:
            out.append((t.expires_at, t.claim_ref, "EXPIRING"))
        elif now - t.checked_at > freshness:
            out.append((t.checked_at + freshness, t.claim_ref, "STALE"))
    return [(ref, why) for _, ref, why in sorted(out)]


def unreachable(
    state: LifecycleState, *, last_confirmed: datetime, now: datetime, grace: timedelta
) -> tuple[CredentialStatus, bool]:
    """(status, confirmed): an unreachable source keeps the last status but, after the grace period, it is no longer
    CONFIRMED. The status itself is never flipped by silence."""
    return state.status, now - last_confirmed <= grace


class ProofStatus(StrEnum):
    """What a stored proof response says about itself when it is read again (T20): never a cached ACTIVE."""

    CURRENT = "CURRENT"
    EXPIRED = "EXPIRED"  # its own validity ended
    RECHECK_REQUIRED = (
        "RECHECK_REQUIRED"  # the source answer is older than the freshness the pack allows
    )


DEFAULT_FRESHNESS = timedelta(days=365)  # the same default as claim_checks.Gate.freshness_days


def _instant(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value:
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def proof_status(
    contract: Mapping[str, Any], *, now: datetime, freshness: timedelta = DEFAULT_FRESHNESS
) -> ProofStatus:
    """Judge a stored proof response at read time. A validity that has ended is EXPIRED; a source answer older than the
    freshness (or one whose time cannot be read) is RECHECK_REQUIRED. Neither is ever reported as current."""
    ends = _instant(contract.get("expires_at"))
    if ends is not None and now >= ends:
        return ProofStatus.EXPIRED
    checked = _instant(contract.get("source_checked_at"))
    if checked is None or now - checked > freshness:
        return ProofStatus.RECHECK_REQUIRED
    return ProofStatus.CURRENT
