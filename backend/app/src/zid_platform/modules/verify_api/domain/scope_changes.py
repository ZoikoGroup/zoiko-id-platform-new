"""Versioned application scopes with staged removal (ZID-CP-WF10 v2.0, WF-10.03; CR-173). Pure domain, no I/O.

What an application may do is a versioned set, not a field that is overwritten. Rules:

- scopes come from the closed list the platform defines; there is no wildcard and no scope can be invented. The one scope that asks for production (applications:submit) is never
  added through self-service: it is requested through the go-live gate;
- narrowing is always safe: a removal can take effect now or at a stated later time (up to 30 days, so a partner can finish a migration), and needs no approval;
- widening a PRODUCTION application needs a second person: the person who proposed an addition can never be the one who approves it. Widening a sandbox application takes
  effect at once;
- an application always keeps at least one scope;
- the scopes in force at a moment are those of the newest change that was effective by then and (if it needed approval) was approved by someone else. A token's own scopes are
  intersected with them on every call, so a removed scope is refused on the next request even for a token issued before the removal.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from zid_platform.modules.verify_api.domain.model import SCOPES, PartnerError

MAX_STAGE = timedelta(days=30)
NEVER_SELF_SERVICE = frozenset({"applications:submit"})
KINDS = ("ADD", "REMOVE", "REPLACE")


@dataclass(frozen=True, slots=True)
class Change:
    kind: str
    resulting_scopes: tuple[str, ...]
    requires_approval: bool
    effective_at: datetime


def plan(current: Sequence[str], wanted: Sequence[str], *, production: bool, now: datetime, effective_at: datetime | None = None) -> Change:
    """The change that takes `current` to `wanted`, or a PartnerError saying why not."""
    want = tuple(dict.fromkeys(s.strip() for s in wanted if s.strip()))
    if not want:
        raise PartnerError("VALIDATION_FAILED", "An application must keep at least one scope.")
    unknown = [s for s in want if s not in SCOPES]
    if unknown:
        raise PartnerError("VALIDATION_FAILED", f"Unknown scope: {unknown[0]}. There are no wildcard scopes.")
    have = set(current)
    added, removed = [s for s in want if s not in have], [s for s in current if s not in want]
    if not added and not removed:
        raise PartnerError("VALIDATION_FAILED", "That is the scope set the application already has.")
    forbidden = [s for s in added if s in NEVER_SELF_SERVICE]
    if forbidden:
        raise PartnerError("VALIDATION_FAILED", f"{forbidden[0]} is requested through the go-live process, not here.")
    when = effective_at or now
    if when < now - timedelta(minutes=1):
        raise PartnerError("VALIDATION_FAILED", "A scope change cannot take effect in the past.")
    if when > now + MAX_STAGE:
        raise PartnerError("VALIDATION_FAILED", f"A scope change can be staged at most {MAX_STAGE.days} days ahead.")
    if added and when > now + timedelta(minutes=1):
        raise PartnerError("VALIDATION_FAILED", "Only a removal can be staged for later; an addition takes effect when it is approved.")
    kind = "REPLACE" if added and removed else ("ADD" if added else "REMOVE")
    return Change(kind, want, requires_approval=bool(added) and production, effective_at=max(when, now))


def approval_problems(proposed_by: str, approver: str, already_approved_by: Sequence[str]) -> list[str]:
    out: list[str] = []
    if approver == proposed_by:
        out.append("PROPOSER_CANNOT_APPROVE_OWN_CHANGE")
    if approver in already_approved_by:
        out.append("ALREADY_APPROVED_BY_THIS_PERSON")
    return out


def effective_scopes(base: Sequence[str], token_scopes: Sequence[str], current_change_scopes: Sequence[str] | None) -> tuple[str, ...]:
    """What a token may do now: the scopes it was issued with, narrowed to those the application holds at this moment. Never wider than either."""
    holds = set(current_change_scopes) if current_change_scopes is not None else set(base)
    return tuple(s for s in token_scopes if s in holds)


def view(changes: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    return list(changes)
