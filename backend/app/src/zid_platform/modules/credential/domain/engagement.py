"""The Zoikorum professional-engagement decision (ZID-RCG-003 section 10.1). Pure rules, no I/O.

Five steps, each with its own output and never one composite VERIFIED:
1. subject and engagement: the professional, role, proposed services, place and period, as Zoikorum supplied them;
2. credential truth: every check (identity, degree, license, insurance, employment, ...) keeps its own result;
3. regulatory rules: the approved pack for the market and profession, its evidence minimum and restrictions;
4. a revocable, audience-bound answer with an expiry and the evidence it rests on;
5. when it must be refreshed.
The mandatory counterexample holds by construction: a genuine degree with no practice right in the engagement's state is
QUALIFICATION_VERIFIED and PRACTICE_ELIGIBILITY_NOT_ESTABLISHED. Whether to accept the engagement is Zoikorum's decision, never ours.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

POSITIVE = frozenset({"VERIFIED", "VERIFIED_LIMITED"})
QUALIFICATION_CHECKS = ("degree", "certification")


@dataclass(frozen=True)
class Engagement:
    professional_ref: str
    role: str
    services: tuple[str, ...]
    country: str
    subdivision: str | None
    profession: str
    period_from: datetime
    period_to: datetime | None


@dataclass(frozen=True)
class Task:
    check: str
    outcome: str | None
    evidence_ref: str | None


@dataclass(frozen=True)
class Pack:
    """The governing pack as the decision needs it. applicable False carries the resolver's reason."""

    applicable: bool
    reason: str
    pack_ref: str | None = None
    evidence_minimum: tuple[str, ...] = ()
    validity_days: int | None = None


@dataclass(frozen=True)
class Decision:
    qualification: str  # QUALIFICATION_VERIFIED | QUALIFICATION_NOT_ESTABLISHED
    practice_eligibility: (
        str  # PRACTICE_ELIGIBILITY_ESTABLISHED | PRACTICE_ELIGIBILITY_NOT_ESTABLISHED
    )
    reasons: tuple[str, ...]
    results: dict[str, str | None]
    evidence_refs: tuple[str, ...]
    expires_at: datetime
    refresh_by: datetime


def _ok(t: Task | None) -> bool:
    return t is not None and t.outcome in POSITIVE


def decide(
    engagement: Engagement,
    tasks: Sequence[Task],
    pack: Pack,
    *,
    case_expires_at: datetime,
    now: datetime,
) -> Decision:
    by: Mapping[str, Task] = {t.check: t for t in tasks}
    reasons: list[str] = []

    qualified = any(_ok(by.get(c)) for c in QUALIFICATION_CHECKS)
    if not qualified:
        reasons.append("QUALIFICATION_NOT_ESTABLISHED")

    practice = True
    if not _ok(by.get("license")):
        practice = False
        reasons.append("LICENSE_NOT_VERIFIED")
    if not pack.applicable:
        practice = False
        reasons.append(f"NO_APPROVED_PACK:{pack.reason}")
    for need in pack.evidence_minimum:
        if not _ok(by.get(need.lower())):
            practice = False
            reasons.append(f"EVIDENCE_MISSING:{need}")
    if engagement.period_to is not None and engagement.period_to <= now:
        practice = False
        reasons.append("ENGAGEMENT_PERIOD_ENDED")
    if not practice:
        reasons.append("PRACTICE_ELIGIBILITY_NOT_ESTABLISHED")

    expires = case_expires_at
    if pack.applicable and pack.validity_days:
        expires = min(expires, now + timedelta(days=pack.validity_days))
    refresh_by = max(now, expires - timedelta(days=1))
    return Decision(
        qualification="QUALIFICATION_VERIFIED" if qualified else "QUALIFICATION_NOT_ESTABLISHED",
        practice_eligibility=(
            "PRACTICE_ELIGIBILITY_ESTABLISHED"
            if practice
            else "PRACTICE_ELIGIBILITY_NOT_ESTABLISHED"
        ),
        reasons=tuple(dict.fromkeys(reasons)),
        results={t.check: t.outcome for t in tasks},
        evidence_refs=tuple(t.evidence_ref for t in tasks if t.evidence_ref and _ok(t)),
        expires_at=expires,
        refresh_by=refresh_by,
    )
