"""Ongoing screening watches and rescreening (ZID-DTV-WF-06 S12, ENG-14, QA-032, QA-048, QA-050, QA-051; CR-113). Pure domain, no I/O.

A watch says who is watched, for which classes, on which triggers and at what cadence. A trigger produces work only for the watches it can change, once per event
(the same list release twice is the same work), and never faster than the tenant's storm limit. Freshness is kept per source as two separate facts, the last ATTEMPT and the last
SUCCESS, and "monitored continuously" is claimable only while every source has a success inside the cadence: a scheduler that ran and failed is not monitoring.

A new list release is compared with the one before. A removed entry (a delisting) or a changed one reopens the cases that relied on it as a new, governed version; the earlier case
record is never erased.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum


class Trigger(StrEnum):
    LIST_UPDATE = "LIST_UPDATE"
    IDENTITY_CHANGE = "IDENTITY_CHANGE"
    UBO_GRAPH_CHANGE = "UBO_GRAPH_CHANGE"
    PROVIDER_EVENT = "PROVIDER_EVENT"
    SCHEDULE = "SCHEDULE"


@dataclass(frozen=True, slots=True)
class Watermark:
    """Per source: when a screening last tried, and when one last completed against a validated release."""

    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    snapshot: str | None = None


@dataclass(frozen=True, slots=True)
class Watch:
    watch_id: str
    tenant_id: str
    subject_ref: str
    purpose: str
    classes: frozenset[str]  # SANCTIONS, PEP_RCA, ADVERSE_MEDIA, OWNERSHIP_CONTROL
    sources: frozenset[str]  # the source ids it depends on
    triggers: frozenset[Trigger]
    cadence: timedelta
    active: bool = False
    providers: frozenset[str] = (
        frozenset()
    )  # paid providers it may use; empty means official sources only
    approved_by: tuple[str, ...] = ()
    paid_authorised_by: str | None = None
    watermarks: Mapping[str, Watermark] = field(default_factory=dict)


def activation_problems(sub: Watch, *, gates_signed: bool) -> list[str]:
    """Why a watch cannot be switched on. Empty means it may be."""
    out: list[str] = []
    if not gates_signed:
        out.append("RELEASE_GATES_NOT_SIGNED")
    if len(set(sub.approved_by)) < 2:
        out.append("TWO_APPROVERS_REQUIRED")
    if sub.providers and not sub.paid_authorised_by:
        out.append(
            "FINANCE_AUTHORISATION_MISSING"
        )  # monitoring fees count against the spend cap, so a paid watch needs Finance
    if not sub.classes:
        out.append("NO_CLASSES")
    if not sub.sources:
        out.append("NO_SOURCES")
    if not sub.triggers:
        out.append("NO_TRIGGERS")
    if sub.cadence <= timedelta(0):
        out.append("CADENCE_INVALID")
    return out


@dataclass(frozen=True, slots=True)
class Event:
    trigger: Trigger
    tenant_id: str | None = None  # None: platform-wide (a list release)
    subject_ref: str | None = None
    source_id: str | None = None
    source_version: str | None = None  # the release digest or id
    event_id: str | None = None


@dataclass(frozen=True, slots=True)
class Work:
    key: str  # one key per (watch, event): the same event is the same work
    watch_id: str
    tenant_id: str
    subject_ref: str
    trigger: Trigger
    reason: str


def _key(sub_id: str, e: Event) -> str:
    return hashlib.sha256(
        f"{sub_id}|{e.trigger.value}|{e.source_id or ''}|{e.source_version or ''}|{e.event_id or ''}|{e.subject_ref or ''}".encode()
    ).hexdigest()[:32]


def affected(sub: Watch, e: Event) -> bool:
    if not sub.active or e.trigger not in sub.triggers:
        return False
    if e.tenant_id is not None and e.tenant_id != sub.tenant_id:
        return False
    if e.trigger is Trigger.LIST_UPDATE:
        return e.source_id in sub.sources
    if e.trigger in (Trigger.IDENTITY_CHANGE, Trigger.UBO_GRAPH_CHANGE, Trigger.PROVIDER_EVENT):
        return e.subject_ref == sub.subject_ref
    return True


def plan(
    subs: Iterable[Watch],
    event: Event,
    *,
    already_queued: Iterable[str] = (),
    queued_for_tenant: Mapping[str, int] | None = None,
    storm_limit: int = 500,
) -> tuple[list[Work], list[Work]]:
    """(work to queue, work deferred). Only affected watches; nothing already queued under the same key; and no tenant gets more than the storm limit in one pass, the rest
    deferred and visible, never dropped."""
    seen = set(already_queued)
    load = dict(queued_for_tenant or {})
    queue: list[Work] = []
    deferred: list[Work] = []
    for sub in sorted(subs, key=lambda s: s.watch_id):
        if not affected(sub, event):
            continue
        key = _key(sub.watch_id, event)
        if key in seen:
            continue
        seen.add(key)
        work = Work(
            key,
            sub.watch_id,
            sub.tenant_id,
            sub.subject_ref,
            event.trigger,
            event.source_id or event.trigger.value,
        )
        if load.get(sub.tenant_id, 0) >= storm_limit:
            deferred.append(work)
        else:
            load[sub.tenant_id] = load.get(sub.tenant_id, 0) + 1
            queue.append(work)
    return queue, deferred


def due(subs: Iterable[Watch], now: datetime) -> list[str]:
    """Watches whose cadence has run out for at least one source: a SUCCESS older than the cadence, or none at all."""
    out: list[str] = []
    for sub in subs:
        if not sub.active or Trigger.SCHEDULE not in sub.triggers:
            continue
        for source in sorted(sub.sources):
            wm = sub.watermarks.get(source, Watermark())
            if wm.last_success_at is None or now - wm.last_success_at >= sub.cadence:
                out.append(sub.watch_id)
                break
    return out


def attempt(wm: Watermark, *, now: datetime) -> Watermark:
    return Watermark(now, wm.last_success_at, wm.snapshot)


def success(wm: Watermark, *, now: datetime, snapshot: str) -> Watermark:
    return Watermark(now, now, snapshot)


def may_claim_continuous(sub: Watch, now: datetime) -> bool:
    """ "Monitored continuously" is shown only while every source the watch depends on has a completed screening inside the cadence. An attempt, however recent, is not one."""
    if not sub.active or not sub.sources:
        return False
    for source in sub.sources:
        wm = sub.watermarks.get(source)
        if wm is None or wm.last_success_at is None or now - wm.last_success_at > sub.cadence:
            return False
    return True


@dataclass(frozen=True, slots=True)
class ListDelta:
    added: frozenset[str]
    removed: frozenset[str]
    changed: frozenset[str]

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed)


def diff(old: Mapping[str, str], new: Mapping[str, str]) -> ListDelta:
    """Compare two releases of one list: each maps a source reference to a digest of that entry."""
    return ListDelta(
        frozenset(new) - frozenset(old),
        frozenset(old) - frozenset(new),
        frozenset(r for r in set(old) & set(new) if old[r] != new[r]),
    )


@dataclass(frozen=True, slots=True)
class OpenCase:
    case_id: str
    relied_on: frozenset[
        str
    ]  # source references of the entries this case matched, cleared as a false positive, or was held on


def reopen(delta: ListDelta, cases: Sequence[OpenCase]) -> list[tuple[str, str]]:
    """(case id, reason) for every case a release changes. A delisting or a changed entry reopens the case that relied on it; a new entry is a rescreen of everyone the source covers, not a
    reopening, so it is not listed here. Reopening starts a new governed version: the earlier record stays."""
    out: list[tuple[str, str]] = []
    for c in cases:
        if c.relied_on & delta.removed:
            out.append((c.case_id, "ENTRY_DELISTED"))
        elif c.relied_on & delta.changed:
            out.append((c.case_id, "ENTRY_CHANGED"))
    return out
