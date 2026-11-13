"""Exact-scope suspension: the provider kill switch, its impact and its rollback (ZID-RCG-003 RCG-G08, WF-06, section 08.2; T08; CR-078). Pure domain.

Rules, each with a test:
- a suspension names a provider and at least one narrower field (service, scheme, country, subdivision, purpose, profession, credential class).
  It stops exactly the invocations that match every field it names and nothing else: one service suspended never disables the provider's unrelated,
  legally authorized services. A provider-wide freeze is allowed only for a breach or severe data issue (08.2 table);
- it takes effect at once and is always enforced: there is no shadow mode for a kill switch;
- it is versioned and append-only. Lifting it (the rollback) is a new version, by a different person from the one who applied it, with a reason and an
  evidence reference. History is never rewritten: asked about a past moment, the answer is the version in force then (T08);
- the impact of a scope is computed before and after it is applied: the launched markets, entitlements, certification scopes and public claims it
  touches, so people know what to review.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from enum import StrEnum

CLOCK_SKEW = timedelta(minutes=5)
REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#@+=-]{2,255}$")
SUSPENSION_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")
MIN_REASON = 10


class SuspensionError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class Trigger(StrEnum):
    CERTIFICATION_WITHDRAWN = "CERTIFICATION_WITHDRAWN"  # registry certificate expired or suspended
    PROVIDER_BREACH = "PROVIDER_BREACH"  # breach or severe data issue: the only trigger that may freeze a whole provider
    ENTITLEMENT_WITHDRAWN = "ENTITLEMENT_WITHDRAWN"  # official issuer data entitlement withdrawn
    TRUST_LIST_STALE = "TRUST_LIST_STALE"  # document trust list cannot refresh
    LICENSE_SUSPENDED = "LICENSE_SUSPENDED"  # practice board marks a licence suspended
    MANUAL = "MANUAL"


class Status(StrEnum):
    ACTIVE = "ACTIVE"
    LIFTED = "LIFTED"


@dataclass(frozen=True)
class Scope:
    """What a suspension covers. None means "any value" for that field; the provider is always named."""

    provider: str
    service_id: str | None = None
    scheme: str | None = None
    country: str | None = None
    subdivision: str | None = None
    purpose: str | None = None
    profession: str | None = None
    credential_class: str | None = None

    def narrower_fields(self) -> list[str]:
        return [
            f.name
            for f in fields(self)
            if f.name != "provider" and getattr(self, f.name) is not None
        ]


@dataclass(frozen=True)
class Invocation:
    """One call that is about to be made, described with the same fields. A field the caller cannot say is None."""

    provider: str
    service_id: str | None = None
    scheme: str | None = None
    country: str | None = None
    subdivision: str | None = None
    purpose: str | None = None
    profession: str | None = None
    credential_class: str | None = None


@dataclass(frozen=True)
class Suspension:
    suspension_id: str
    version: int
    scope: Scope
    trigger: Trigger
    status: Status
    reason: str
    evidence_ref: str  # an incident, a register snapshot, a ticket
    effective_at: datetime
    recorded_by: str
    recorded_at: datetime


def matches(scope: Scope, call: Invocation) -> bool:
    """Every field the scope names must equal the call's; a field the call cannot say (None) does NOT match a named one, so a suspension of
    the GB service never stops a call whose country is unknown, and a call is never stopped by a guess."""
    if scope.provider != call.provider:
        return False
    for name in scope.narrower_fields():
        if getattr(call, name) != getattr(scope, name):
            return False
    return True


def check_new(s: Suspension, *, now: datetime) -> None:
    """The rules for applying a suspension (version 1)."""
    if not SUSPENSION_ID.match(s.suspension_id):
        raise SuspensionError("SUSPENSION_ID_INVALID", s.suspension_id)
    if s.version != 1 or s.status is not Status.ACTIVE:
        raise SuspensionError("SUSPENSION_OUT_OF_ORDER", "a new suspension is version 1 and ACTIVE")
    if not s.scope.provider.strip():
        raise SuspensionError("SUSPENSION_PROVIDER_REQUIRED")
    if not s.scope.narrower_fields() and s.trigger is not Trigger.PROVIDER_BREACH:
        raise SuspensionError(
            "SUSPENSION_TOO_BROAD",
            "name the service, scheme, country or other field; a whole provider is frozen only for a breach",
        )
    _common(s, now=now)


def check_lift(lift: Suspension, *, previous: Sequence[Suspension], now: datetime) -> None:
    """The rules for lifting (the rollback): the next version, LIFTED, same scope, by someone other than whoever applied it."""
    if not previous:
        raise SuspensionError("SUSPENSION_NOT_FOUND", lift.suspension_id)
    last = previous[-1]
    if last.status is not Status.ACTIVE:
        raise SuspensionError("SUSPENSION_NOT_ACTIVE", lift.suspension_id)
    if lift.version != last.version + 1 or lift.status is not Status.LIFTED:
        raise SuspensionError(
            "SUSPENSION_OUT_OF_ORDER", f"expected version {last.version + 1}, LIFTED"
        )
    if lift.scope != last.scope or lift.trigger is not last.trigger:
        raise SuspensionError(
            "SUSPENSION_SCOPE_CHANGED",
            "a lift covers exactly what was suspended; suspend a new scope instead",
        )
    if lift.recorded_by == last.recorded_by:
        raise SuspensionError(
            "SUSPENSION_LIFTED_BY_APPLIER", "a different person lifts a suspension"
        )
    if lift.effective_at < last.effective_at:
        raise SuspensionError(
            "SUSPENSION_OUT_OF_ORDER", "a lift cannot take effect before the suspension"
        )
    _common(lift, now=now)


def _common(s: Suspension, *, now: datetime) -> None:
    if len(s.reason.strip()) < MIN_REASON:
        raise SuspensionError(
            "SUSPENSION_REASON_REQUIRED", f"say why, in at least {MIN_REASON} characters"
        )
    if not REFERENCE.match(s.evidence_ref):
        raise SuspensionError(
            "SUSPENSION_EVIDENCE_REQUIRED", "name the incident, register snapshot or ticket"
        )
    if not s.recorded_by.strip():
        raise SuspensionError("SUSPENSION_RECORDER_REQUIRED")
    for t in (s.effective_at, s.recorded_at):
        if t.tzinfo is None:
            raise SuspensionError("SUSPENSION_TIME_INVALID", "times carry a time zone")
    if s.effective_at > now + CLOCK_SKEW:
        raise SuspensionError(
            "SUSPENSION_FROM_THE_FUTURE", "a kill switch takes effect now, never later"
        )


def in_force(history: Iterable[Suspension], at: datetime) -> list[Suspension]:
    """The suspensions ACTIVE at `at`: for each suspension id, the latest version effective by then, if it is ACTIVE."""
    latest: dict[str, Suspension] = {}
    for s in history:
        if s.effective_at <= at and (
            s.suspension_id not in latest or s.version > latest[s.suspension_id].version
        ):
            latest[s.suspension_id] = s
    return sorted(
        (s for s in latest.values() if s.status is Status.ACTIVE), key=lambda s: s.suspension_id
    )


def blocking(history: Iterable[Suspension], call: Invocation, at: datetime) -> list[Suspension]:
    """The suspensions that stop this call at `at`. Empty means the kill switch does not apply."""
    return [s for s in in_force(history, at) if matches(s.scope, call)]


@dataclass(frozen=True)
class Impact:
    """What a scope touches, so people know what to review. Each list holds labels, never personal data."""

    markets: tuple[str, ...] = ()
    entitlements: tuple[str, ...] = ()
    certifications: tuple[str, ...] = ()
    claims: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.markets or self.entitlements or self.certifications or self.claims)


def impact(
    scope: Scope,
    *,
    markets: Iterable[tuple[str, Invocation]] = (),
    entitlements: Iterable[tuple[str, Invocation]] = (),
    certifications: Iterable[tuple[str, Invocation]] = (),
    claims: Iterable[tuple[str, Sequence[Invocation]]] = (),
) -> Impact:
    """Each candidate is (label, the invocation it stands for); a claim may rest on several. A candidate is affected when the scope matches it."""
    return Impact(
        markets=tuple(sorted({label for label, call in markets if matches(scope, call)})),
        entitlements=tuple(sorted({label for label, call in entitlements if matches(scope, call)})),
        certifications=tuple(
            sorted({label for label, call in certifications if matches(scope, call)})
        ),
        claims=tuple(
            sorted({label for label, calls in claims if any(matches(scope, c) for c in calls)})
        ),
    )
