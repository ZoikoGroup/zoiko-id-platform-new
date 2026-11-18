"""Biometric method lifecycle and kill switch (ZID-DTV-WF-04 section 06, BIO-03; QA-001, QA-015, QA-043; CR-107). Pure domain, no I/O.

The engine gate (`biometric_engine.engine_gate`) says whether an engine's evaluation record is complete. It does not say WHERE the engine may be used,
by WHOM, or whether someone has pulled the switch. This module is that: one versioned state per exact scope, and the rules for moving it.

A scope is one biometric method (face match, or liveness / presentation-attack detection) run by one engine id and version, for one country and one
purpose, optionally for one tenant. States, least to most permissive:

    OFF, PAUSED            never used for a live session (PAUSED is the kill switch: someone stopped it)
    LAB_ONLY               evaluation fixtures only; a live session is refused with METHOD_LAB_ONLY (WF01 T02)
    PILOT_GATED            live, but only for the one tenant the scope names
    PRODUCTION_ELIGIBLE    live for the scope; every other gate (engine, DPIA, country overlay, consent) still applies at run time

Rules, each with a test:
- no scope is ever on because nothing was recorded: a scope with no record is OFF;
- moving to a MORE permissive state is two people: one proposes, a different one approves, and the approval applies only to the version the proposal
  was made against (a state that moved in between makes the proposal stale). No step is skipped: OFF or PAUSED goes to LAB_ONLY first, then
  PILOT_GATED, then PRODUCTION_ELIGIBLE. A resume from PAUSED is therefore a fresh approval, never an undo;
- PILOT_GATED names a tenant; PILOT_GATED and PRODUCTION_ELIGIBLE need a DPIA reference and an independent evaluation reference;
- moving to a LESS permissive state (pause, step down, retire to OFF) is one person, at once, with a reason and an evidence reference: making things
  safer is never held up by a second approver;
- versions are append-only; history is never rewritten, and a past moment is answered by the version in force then;
- PRODUCTION_ELIGIBLE carries an exposure percentage (CR-110, B11 signed release decision): the share of people the method runs for,
  chosen by a stable hash of the scope and the person, so one person always gets the same answer. Widening it is a proposal and a second
  person; narrowing it is a step down, one person, at once. Every approved version carries a decision hash over exactly what was approved;
- when several scopes match a live call (a tenant's own scope and the all-tenant one), a PAUSED one always wins; otherwise the tenant's own scope wins.

What this does NOT do: decide that a DPIA is approved (the privacy module's release gate does, at run time, for the tenant asking), evaluate an engine,
or apply the country overlay. The eligibility service combines them (`application/biometric_eligibility.py`).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

CLOCK_SKEW = timedelta(minutes=5)
PROPOSAL_TTL = timedelta(
    days=14
)  # PROVISIONAL: an unanswered proposal lapses; the owners may change it
MIN_REASON = 10
SCOPE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")
ENGINE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
COUNTRY = re.compile(r"^[A-Z]{2}$")
CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#@+=-]{2,255}$")


class MethodError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class Method(StrEnum):
    FACE_MATCH = "FACE_MATCH"  # 1:1 live face against the document or chip portrait
    LIVENESS = "LIVENESS"  # presentation-attack detection on the live capture


class State(StrEnum):
    OFF = "OFF"
    PAUSED = "PAUSED"
    LAB_ONLY = "LAB_ONLY"
    PILOT_GATED = "PILOT_GATED"
    PRODUCTION_ELIGIBLE = "PRODUCTION_ELIGIBLE"


RANK = {
    State.OFF: 0,
    State.PAUSED: 0,
    State.LAB_ONLY: 1,
    State.PILOT_GATED: 2,
    State.PRODUCTION_ELIGIBLE: 3,
}
# The one step up from each state. A resume from PAUSED starts again at LAB_ONLY.
NEXT_UP = {
    State.OFF: State.LAB_ONLY,
    State.PAUSED: State.LAB_ONLY,
    State.LAB_ONLY: State.PILOT_GATED,
    State.PILOT_GATED: State.PRODUCTION_ELIGIBLE,
}
NEEDS_EVIDENCE = frozenset({State.PILOT_GATED, State.PRODUCTION_ELIGIBLE})


class ProposalStatus(StrEnum):
    OPEN = "OPEN"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class Scope:
    # a biometric Method here; the WF-05 check methods (`check_methods.CheckMethod`) reuse the same lifecycle with their own enum and tables (CR-112)
    method: StrEnum
    engine_id: str
    engine_version: str
    country: str
    purpose: str
    tenant_id: str | None = None  # None: every tenant

    def key(self) -> tuple[str, str, str, str, str, str | None]:
        return (
            self.method.value,
            self.engine_id,
            self.engine_version,
            self.country,
            self.purpose,
            self.tenant_id,
        )


@dataclass(frozen=True, slots=True)
class Record:
    """One version of a scope's state."""

    scope_id: str
    version: int
    scope: Scope
    state: State
    dpia_ref: str | None
    evaluation_ref: str | None
    reason: str
    evidence_ref: str
    recorded_by: str  # who made this version: the approver of a step up, the actor of a step down
    proposed_by: str | None  # the proposer of a step up; None for a step down
    proposal_id: str | None
    effective_at: datetime
    recorded_at: datetime
    exposure_percent: int = 100  # CR-110: below 100 only in PRODUCTION_ELIGIBLE
    decision_hash: str | None = None  # CR-110: the signed release decision, on an approved step up


@dataclass(frozen=True, slots=True)
class Proposal:
    proposal_id: str
    scope_id: str
    scope: Scope
    from_version: int  # 0 when the scope has no record yet
    target: State
    dpia_ref: str | None
    evaluation_ref: str | None
    reason: str
    evidence_ref: str
    proposed_by: str
    proposed_at: datetime
    status: ProposalStatus = ProposalStatus.OPEN
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None
    exposure_percent: int = 100  # CR-110


def current(history: Sequence[Record]) -> Record | None:
    return max(history, key=lambda r: r.version) if history else None


def state_of(history: Sequence[Record]) -> State:
    last = current(history)
    return last.state if last is not None else State.OFF


# --------------------------------------------------------------------------------------------------------------------------------- checks


def check_scope(s: Scope) -> None:
    if not ENGINE_ID.match(s.engine_id):
        raise MethodError("METHOD_ENGINE_ID_INVALID", s.engine_id)
    if not s.engine_version.strip() or len(s.engine_version) > 64:
        raise MethodError("METHOD_ENGINE_VERSION_INVALID")
    if not COUNTRY.match(s.country):
        raise MethodError("METHOD_COUNTRY_INVALID", s.country)
    if not CODE.match(s.purpose):
        raise MethodError("METHOD_PURPOSE_INVALID", s.purpose)
    if s.tenant_id is not None and not s.tenant_id.strip():
        raise MethodError("METHOD_TENANT_INVALID")


def _common(*, reason: str, evidence_ref: str, actor: str, at: datetime, now: datetime) -> None:
    if len(reason.strip()) < MIN_REASON:
        raise MethodError("METHOD_REASON_REQUIRED", f"say why, in at least {MIN_REASON} characters")
    if not REFERENCE.match(evidence_ref):
        raise MethodError("METHOD_EVIDENCE_REQUIRED", "name the ticket, report or incident")
    if not actor.strip():
        raise MethodError("METHOD_ACTOR_REQUIRED")
    if at.tzinfo is None:
        raise MethodError("METHOD_TIME_INVALID", "times carry a time zone")
    if at > now + CLOCK_SKEW:
        raise MethodError("METHOD_FROM_THE_FUTURE")


def check_proposal(p: Proposal, *, history: Sequence[Record], now: datetime) -> None:
    """A step up, proposed against the scope's current version. It changes nothing until someone else approves it."""
    if not SCOPE_ID.match(p.scope_id):
        raise MethodError("METHOD_SCOPE_ID_INVALID", p.scope_id)
    check_scope(p.scope)
    last = current(history)
    if last is not None and last.scope != p.scope:
        raise MethodError("METHOD_SCOPE_CHANGED", "a scope id always names the same scope")
    if p.from_version != (last.version if last else 0):
        raise MethodError(
            "METHOD_PROPOSAL_STALE", "the state moved; propose against the current version"
        )
    now_state = last.state if last else State.OFF
    if not 1 <= p.exposure_percent <= 100:
        raise MethodError("METHOD_EXPOSURE_INVALID", "1 to 100 percent")
    if p.target is not State.PRODUCTION_ELIGIBLE and p.exposure_percent != 100:
        raise MethodError("METHOD_EXPOSURE_ONLY_FOR_PRODUCTION")
    widening = (
        last is not None
        and now_state is State.PRODUCTION_ELIGIBLE
        and p.target is State.PRODUCTION_ELIGIBLE
        and p.exposure_percent > last.exposure_percent
    )
    if not widening and NEXT_UP.get(now_state) is not p.target:
        raise MethodError(
            "METHOD_STEP_NOT_ALLOWED",
            f"from {now_state.value} the only step up is {NEXT_UP.get(now_state, 'none')}",
        )
    if p.target is State.PILOT_GATED and p.scope.tenant_id is None:
        raise MethodError("METHOD_PILOT_NEEDS_TENANT", "a pilot names the one tenant it runs for")
    if p.target in NEEDS_EVIDENCE:
        if not (p.dpia_ref and REFERENCE.match(p.dpia_ref)):
            raise MethodError("METHOD_DPIA_REFERENCE_REQUIRED")
        if not (p.evaluation_ref and REFERENCE.match(p.evaluation_ref)):
            raise MethodError("METHOD_EVALUATION_REFERENCE_REQUIRED")
    if p.status is not ProposalStatus.OPEN:
        raise MethodError("METHOD_PROPOSAL_OUT_OF_ORDER", "a new proposal is OPEN")
    _common(
        reason=p.reason, evidence_ref=p.evidence_ref, actor=p.proposed_by, at=p.proposed_at, now=now
    )


def approve(
    p: Proposal,
    *,
    history: Sequence[Record],
    by: str,
    reason: str,
    now: datetime,
) -> tuple[Proposal, Record]:
    """The second person. Returns the decided proposal and the new version it makes."""
    if p.status is not ProposalStatus.OPEN:
        raise MethodError("METHOD_PROPOSAL_NOT_OPEN", p.proposal_id)
    if by == p.proposed_by:
        raise MethodError("METHOD_APPROVED_BY_PROPOSER", "a different person approves a step up")
    if now - p.proposed_at > PROPOSAL_TTL:
        raise MethodError("METHOD_PROPOSAL_EXPIRED", "propose again")
    last = current(history)
    if p.from_version != (last.version if last else 0):
        raise MethodError("METHOD_PROPOSAL_STALE", "the state moved after it was proposed")
    _common(reason=reason, evidence_ref=p.evidence_ref, actor=by, at=now, now=now)
    decided = replace(
        p, status=ProposalStatus.APPROVED, decided_by=by, decided_at=now, decision_reason=reason
    )
    record = Record(
        scope_id=p.scope_id,
        version=p.from_version + 1,
        scope=p.scope,
        state=p.target,
        dpia_ref=p.dpia_ref,
        evaluation_ref=p.evaluation_ref,
        reason=reason,
        evidence_ref=p.evidence_ref,
        recorded_by=by,
        proposed_by=p.proposed_by,
        proposal_id=p.proposal_id,
        effective_at=now,
        recorded_at=now,
        exposure_percent=p.exposure_percent,
    )
    return decided, replace(record, decision_hash=decision_hash(record))


def decision_hash(r: Record) -> str:
    """CR-110 (B11): the digest of exactly what was approved: the scope, the state and version, the references, the exposure and both people."""
    body = {
        "scope_id": r.scope_id,
        "version": r.version,
        "scope": list(r.scope.key()),
        "state": r.state.value,
        "dpia_ref": r.dpia_ref,
        "evaluation_ref": r.evaluation_ref,
        "exposure_percent": r.exposure_percent,
        "proposed_by": r.proposed_by,
        "approved_by": r.recorded_by,
        "proposal_id": r.proposal_id,
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def reject(p: Proposal, *, by: str, reason: str, now: datetime) -> Proposal:
    """Anyone who may approve may refuse, including the proposer withdrawing it. Nothing changes state."""
    if p.status is not ProposalStatus.OPEN:
        raise MethodError("METHOD_PROPOSAL_NOT_OPEN", p.proposal_id)
    if len(reason.strip()) < MIN_REASON:
        raise MethodError("METHOD_REASON_REQUIRED", f"say why, in at least {MIN_REASON} characters")
    return replace(
        p, status=ProposalStatus.REJECTED, decided_by=by, decided_at=now, decision_reason=reason
    )


def step_down(
    history: Sequence[Record],
    *,
    target: State,
    by: str,
    reason: str,
    evidence_ref: str,
    now: datetime,
    exposure_percent: int | None = None,
) -> Record:
    """The kill switch (PAUSED), a step down, or retirement (OFF): one person, at once. Only to a less permissive state, or to PAUSED from anything
    that is on. CR-110: PRODUCTION_ELIGIBLE to PRODUCTION_ELIGIBLE with a SMALLER `exposure_percent` narrows the rollout."""
    last = current(history)
    if last is None:
        raise MethodError(
            "METHOD_NOT_FOUND", "nothing is recorded for this scope: it is already OFF"
        )
    if target is State.PAUSED:
        if last.state in (State.OFF, State.PAUSED):
            raise MethodError("METHOD_NOT_ON", f"the scope is {last.state.value}")
    elif target is State.OFF:
        if last.state is State.OFF:
            raise MethodError("METHOD_NOT_ON", "the scope is already OFF")
    elif target is State.PRODUCTION_ELIGIBLE and last.state is State.PRODUCTION_ELIGIBLE:
        if exposure_percent is None or not 1 <= exposure_percent < last.exposure_percent:
            raise MethodError(
                "METHOD_STEP_NOT_DOWN",
                f"narrowing needs an exposure below {last.exposure_percent}%",
            )
    elif last.state is State.PAUSED or RANK[target] >= RANK[last.state]:
        raise MethodError(
            "METHOD_STEP_NOT_DOWN",
            f"from {last.state.value} to {target.value} is not a step down; a step up needs a proposal",
        )
    if target is State.PILOT_GATED and last.scope.tenant_id is None:
        raise MethodError(
            "METHOD_PILOT_NEEDS_TENANT", "an all-tenant scope steps down to LAB_ONLY, PAUSED or OFF"
        )
    _common(reason=reason, evidence_ref=evidence_ref, actor=by, at=now, now=now)
    return Record(
        scope_id=last.scope_id,
        version=last.version + 1,
        scope=last.scope,
        state=target,
        dpia_ref=last.dpia_ref,
        evaluation_ref=last.evaluation_ref,
        reason=reason,
        evidence_ref=evidence_ref,
        recorded_by=by,
        proposed_by=None,
        proposal_id=None,
        effective_at=now,
        recorded_at=now,
        exposure_percent=exposure_percent
        if target is State.PRODUCTION_ELIGIBLE and exposure_percent
        else 100,
    )


# --------------------------------------------------------------------------------------------------------------------------------- runtime


@dataclass(frozen=True, slots=True)
class Call:
    """A live call about to use a method. Every field is known: a call that cannot say its country or purpose gets no biometric method."""

    method: StrEnum
    engine_id: str
    engine_version: str
    country: str
    purpose: str
    tenant_id: str
    rollout_key: str | None = (
        None  # CR-110: the person the session is for; None when asking about the organization (readiness)
    )


def in_rollout(scope_id: str, rollout_key: str, exposure_percent: int) -> bool:
    """CR-110: a stable bucket 0..99 from the scope and the person. The same person always gets the same answer for the same scope."""
    if exposure_percent >= 100:
        return True
    bucket = int(hashlib.sha256(f"{scope_id}|{rollout_key}".encode()).hexdigest()[:8], 16) % 100
    return bucket < exposure_percent


@dataclass(frozen=True, slots=True)
class Availability:
    available: bool
    state: State
    reasons: tuple[str, ...]
    record: Record | None  # the version the answer rests on, when there is one


def in_force(histories: Iterable[Sequence[Record]], at: datetime) -> list[Record]:
    """The version of each scope in force at `at`."""
    out = []
    for h in histories:
        effective = [r for r in h if r.effective_at <= at]
        last = current(effective)
        if last is not None:
            out.append(last)
    return out


def _matches(s: Scope, c: Call) -> bool:
    return (
        s.method is c.method
        and s.engine_id == c.engine_id
        and s.engine_version == c.engine_version
        and s.country == c.country
        and s.purpose == c.purpose
        and (s.tenant_id is None or s.tenant_id == c.tenant_id)
    )


def availability(records: Iterable[Record], call: Call) -> Availability:
    """Whether a live session may use this method now. `records` are the versions in force (`in_force`). PAUSED always wins; then the tenant's own
    scope; then the all-tenant one. No matching record is OFF."""
    matching = [r for r in records if _matches(r.scope, call)]
    paused = sorted((r for r in matching if r.state is State.PAUSED), key=lambda r: r.scope_id)
    if paused:
        return Availability(False, State.PAUSED, ("METHOD_PAUSED",), paused[0])
    own = [r for r in matching if r.scope.tenant_id is not None]
    chosen = own[0] if own else (matching[0] if matching else None)
    if chosen is None:
        return Availability(False, State.OFF, ("METHOD_OFF",), None)
    if chosen.state is State.PRODUCTION_ELIGIBLE:
        if chosen.exposure_percent < 100:
            if (
                call.rollout_key is None
            ):  # the organization's readiness: available to part of the people
                return Availability(
                    True, chosen.state, (f"METHOD_ROLLOUT_{chosen.exposure_percent}",), chosen
                )
            if not in_rollout(chosen.scope_id, call.rollout_key, chosen.exposure_percent):
                return Availability(False, chosen.state, ("METHOD_NOT_IN_ROLLOUT",), chosen)
        return Availability(True, chosen.state, (), chosen)
    if chosen.state is State.PILOT_GATED:
        # a pilot scope always names its tenant, so matching it means this is that tenant
        return Availability(True, chosen.state, ("METHOD_PILOT",), chosen)
    code = {State.LAB_ONLY: "METHOD_LAB_ONLY", State.OFF: "METHOD_OFF"}[chosen.state]
    return Availability(False, chosen.state, (code,), chosen)


__all__ = [
    "NEEDS_EVIDENCE",
    "NEXT_UP",
    "PROPOSAL_TTL",
    "RANK",
    "Availability",
    "Call",
    "Method",
    "MethodError",
    "Proposal",
    "ProposalStatus",
    "Record",
    "Scope",
    "State",
    "approve",
    "availability",
    "check_proposal",
    "check_scope",
    "current",
    "decision_hash",
    "in_force",
    "in_rollout",
    "reject",
    "state_of",
    "step_down",
]
