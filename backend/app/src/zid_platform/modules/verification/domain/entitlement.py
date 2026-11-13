"""Partner entitlement and source authorization (ZID-RCG-003 RCG-G03; sections 05.1, 05.3; CR-074). Pure domain, no I/O.

A provider being certified (attestation.py) is not the same as Zoiko being allowed to use it in a market: the contract, the product licence and
the data-use terms decide that. This module is the dated record of that permission. Rules, each with a test:
- an entitlement is for the exact provider, country, capability and purpose; a neighbouring country, capability or purpose is a different scope and
  nothing is inferred from it;
- ENTITLED means: the latest statement effective at the time asked says GRANTED, and the time is inside its validity dates. No statement, a
  suspension, a withdrawal, a date not yet reached or already passed is DENIED, with the reason. Nothing here ever turns into a pass;
- a grant names the contract and the documented terms it rests on, so source access is never a licence for unrestricted use (document 05.2);
- history is never rewritten: asked about a past moment, the answer comes from the statement that was the latest then;
- a statement is refused if it is from the future, not newer than the one it follows for that scope, or missing its contract or terms reference.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

CLOCK_SKEW = timedelta(minutes=5)
REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#@+=-]{2,255}$")
CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
COUNTRY = re.compile(r"^[A-Z]{2}$")


class EntitlementError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class Status(StrEnum):
    GRANTED = "GRANTED"
    SUSPENDED = "SUSPENDED"
    WITHDRAWN = "WITHDRAWN"


class Outcome(StrEnum):
    ENTITLED = "ENTITLED"
    DENIED = "DENIED"


@dataclass(frozen=True)
class Scope:
    """What an entitlement covers. A neighbouring country, capability or purpose is a different scope."""

    provider: str
    country: str
    capability: str
    purpose: str


@dataclass(frozen=True)
class Grant:
    grant_id: str
    scope: Scope
    status: Status
    effective_at: datetime  # from when this statement holds
    valid_from: datetime
    valid_until: datetime | None
    contract_ref: str  # the contract or order the permission rests on
    terms_ref: str  # the documented terms of source and data use
    recorded_by: str
    recorded_at: datetime


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    reason: str
    grant: Grant | None = None

    @property
    def entitled(self) -> bool:
        return self.outcome is Outcome.ENTITLED


def check_scope(scope: Scope) -> None:
    if not scope.provider.strip() or len(scope.provider) > 128:
        raise EntitlementError("ENTITLEMENT_PROVIDER_REQUIRED")
    if not COUNTRY.match(scope.country):
        raise EntitlementError("ENTITLEMENT_COUNTRY_INVALID", scope.country)
    if not CODE.match(scope.capability) or not CODE.match(scope.purpose):
        raise EntitlementError(
            "ENTITLEMENT_SCOPE_INVALID", "capability and purpose are upper-case codes"
        )


def check_grant(grant: Grant, *, now: datetime, previous: Sequence[Grant] = ()) -> None:
    """The rules for recording a statement; `previous` are the earlier ones for the same scope."""
    check_scope(grant.scope)
    if not grant.recorded_by.strip():
        raise EntitlementError("ENTITLEMENT_RECORDER_REQUIRED")
    if not REFERENCE.match(grant.contract_ref):
        raise EntitlementError("ENTITLEMENT_CONTRACT_REQUIRED", "name the contract or order")
    if not REFERENCE.match(grant.terms_ref):
        raise EntitlementError(
            "ENTITLEMENT_TERMS_REQUIRED", "name the documented source and data-use terms"
        )
    for t in (grant.effective_at, grant.valid_from, grant.valid_until):
        if t is not None and t.tzinfo is None:
            raise EntitlementError("ENTITLEMENT_TIME_INVALID", "times carry a time zone")
    if grant.effective_at > now + CLOCK_SKEW:
        raise EntitlementError(
            "ENTITLEMENT_FROM_THE_FUTURE", "a statement cannot take effect later than now"
        )
    if grant.valid_until is not None and grant.valid_until <= grant.valid_from:
        raise EntitlementError("ENTITLEMENT_DATES_INVALID", "valid_until must be after valid_from")
    if any(p.effective_at >= grant.effective_at for p in previous):
        raise EntitlementError(
            "ENTITLEMENT_OUT_OF_ORDER", "a statement must be newer than the one before it"
        )


def decide(history: Iterable[Grant], scope: Scope, at: datetime) -> Decision:
    """Is this exact scope entitled at the moment `at`? DENIED unless the statement then in force says GRANTED and is inside its dates."""
    in_force = [g for g in history if g.scope == scope and g.effective_at <= at]
    if not in_force:
        return Decision(Outcome.DENIED, "no_entitlement")
    latest = max(in_force, key=lambda g: g.effective_at)
    if latest.status is not Status.GRANTED:
        return Decision(Outcome.DENIED, latest.status.value.lower(), latest)
    if at < latest.valid_from:
        return Decision(Outcome.DENIED, "not_yet_valid", latest)
    if latest.valid_until is not None and at >= latest.valid_until:
        return Decision(Outcome.DENIED, "expired", latest)
    return Decision(Outcome.ENTITLED, "entitled", latest)


def changes(
    before: Mapping[Scope, Decision], after: Mapping[Scope, Decision]
) -> dict[Scope, tuple[Outcome, Outcome]]:
    """The scopes whose decision differs: losing an entitlement affects exactly the scopes it covered."""
    return {
        s: (before[s].outcome, after[s].outcome)
        for s in before.keys() & after.keys()
        if before[s].outcome is not after[s].outcome
    }
