"""Retention, erasure and legal holds for company-verification records (ZID-DTV-WF-02 sections 2, 17, 20; acceptance T23, T24). Pure domain, no I/O.

What is kept, for how long, is the market pack's schedule per record class; nothing is promised beyond it:

- a record is due for deletion when its class has a schedule and the schedule's days have passed since it was created, and no legal hold covers it. A class with NO schedule is never deleted by
  this module and is reported as `NO_SCHEDULE`: an unknown period is not a short one;
- a legal hold on a company, a case or a record class stops deletion of what it covers, whoever asks and whatever the schedule says, until it is released (a hold is lifted, never edited);
- an erasure request (a person's right) is honoured as far as the law allows and no further: every record the request covers is deleted unless a hold covers it or the class has a legal minimum
  that has not yet passed; what stays is listed with the reason, the outcome is `PARTIAL` whenever anything stays, and the answer never says "everything erased" when it was not. Where a record must stay
  as proof, what remains is the non-reconstructive proof (a digest and the decision), never the document;
- backups are not mutable: the plan says when a deleted record leaves them (`backup_horizon_days`), so a promise is made only for that date.

The plan only decides. Deleting is done by the caller through an approved store, and each deletion is recorded in the erasure log.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class Outcome(StrEnum):
    COMPLETE = "COMPLETE"  # everything the request covers is to be deleted
    PARTIAL = "PARTIAL"  # something stays, and the reasons say what and why
    NOTHING_TO_ERASE = "NOTHING_TO_ERASE"


@dataclass(frozen=True, slots=True)
class Record:
    record_id: str
    record_class: str  # RAW_DOCUMENTS | EXTRACTED_DATA | DECISION_METADATA | SCREENING_EVIDENCE
    created_at: datetime
    case_id: str | None = None
    subject: str | None = None  # the person the record is about, when it is about one


@dataclass(frozen=True, slots=True)
class Hold:
    hold_id: str
    tenant_wide: bool = False
    case_id: str | None = None
    record_class: str | None = None
    placed_at: datetime | None = None
    released_at: datetime | None = None

    def active(self, now: datetime) -> bool:
        return self.released_at is None or self.released_at > now

    def covers(self, r: Record) -> bool:
        if self.tenant_wide:
            return True
        if self.case_id is not None and r.case_id != self.case_id:
            return False
        if self.record_class is not None and r.record_class != self.record_class:
            return False
        return self.case_id is not None or self.record_class is not None


@dataclass(frozen=True, slots=True)
class Plan:
    delete: tuple[str, ...]
    kept: tuple[tuple[str, str], ...]  # (record id, reason)
    outcome: Outcome


def _held(r: Record, holds: Iterable[Hold], now: datetime) -> str | None:
    for h in holds:
        if h.active(now) and h.covers(r):
            return f"LEGAL_HOLD:{h.hold_id}"
    return None


def due_for_deletion(
    records: Iterable[Record],
    schedule_days: Mapping[str, int | None],
    holds: Iterable[Hold],
    *,
    now: datetime,
) -> Plan:
    """What the schedule says may go now. A held record and a class with no schedule stay, and the reason is recorded."""
    holds = list(holds)
    delete: list[str] = []
    kept: list[tuple[str, str]] = []
    for r in records:
        days = schedule_days.get(r.record_class)
        if days is None:
            kept.append((r.record_id, "NO_SCHEDULE"))
            continue
        if now - r.created_at < timedelta(days=days):
            continue  # not due yet; not worth a line in the plan
        held = _held(r, holds, now)
        if held:
            kept.append((r.record_id, held))
        else:
            delete.append(r.record_id)
    return Plan(
        tuple(delete),
        tuple(kept),
        Outcome.PARTIAL if kept else (Outcome.COMPLETE if delete else Outcome.NOTHING_TO_ERASE),
    )


def erasure_plan(
    records: Iterable[Record],
    *,
    subject: str,
    legal_minimum_days: Mapping[str, int | None],
    holds: Iterable[Hold],
    now: datetime,
    backup_horizon_days: int,
) -> tuple[Plan, datetime]:
    """The honest answer to a request to erase one person's records: what is deleted, what stays and why, and the date by which a deletion has left the backups. Erasure is not always absolute."""
    holds = list(holds)
    delete: list[str] = []
    kept: list[tuple[str, str]] = []
    for r in records:
        if r.subject != subject:
            continue
        held = _held(r, holds, now)
        if held:
            kept.append((r.record_id, held))
            continue
        minimum = legal_minimum_days.get(r.record_class)
        if minimum is not None and now - r.created_at < timedelta(days=minimum):
            kept.append(
                (
                    r.record_id,
                    f"LEGAL_MINIMUM_RETENTION_UNTIL:{(r.created_at + timedelta(days=minimum)).date().isoformat()}",
                )
            )
            continue
        delete.append(r.record_id)
    outcome = (
        Outcome.NOTHING_TO_ERASE
        if not delete and not kept
        else (Outcome.PARTIAL if kept else Outcome.COMPLETE)
    )
    return Plan(tuple(delete), tuple(kept), outcome), now + timedelta(days=backup_horizon_days)


def promise(plan: Plan) -> str:
    """The wording a person is given. It claims only what the plan does."""
    if plan.outcome is Outcome.NOTHING_TO_ERASE:
        return "We hold no records about you that this request covers."
    if plan.outcome is Outcome.COMPLETE:
        return f"{len(plan.delete)} records will be deleted. Copies leave our backups by the date given."
    return (
        f"{len(plan.delete)} records will be deleted. {len(plan.kept)} must be kept for now, each for a recorded legal reason, and we will say when they can go. "
        "Copies of what is deleted leave our backups by the date given."
    )
