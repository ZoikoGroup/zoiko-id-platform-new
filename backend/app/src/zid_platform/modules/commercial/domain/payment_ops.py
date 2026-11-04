"""Refunds, disputes and reconciliation (ZID-UX-COM-009 F03 and F04; slice L-14f, CR-058). Pure: no I/O.

Four facts are kept apart and never collapsed into one "success": a person approved it, the processor accepted it, the processor says the money moved, and the bank and the
ledger agree. A refund is limited to what the customer overpaid; a provider answer nobody recognises is UNKNOWN, never a success; an outcome of a dispute is the processor's;
and a period is closed only when nothing blocks it and two different people have signed exactly what was reconciled. Money is whole minor units.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class OpsError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail or code


# ------------------------------------------------------------------------------------------------------------------------------- refunds


class RefundState(StrEnum):
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    EXECUTION_PENDING = "EXECUTION_PENDING"
    PROCESSOR_PENDING = "PROCESSOR_PENDING"
    PARTIAL = "PARTIAL"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELED = "CANCELED"


REFUND_FINAL = frozenset(
    {RefundState.SUCCEEDED, RefundState.REJECTED, RefundState.EXPIRED, RefundState.CANCELED}
)
# Money may be on its way back to the customer in these states, so the amount still counts against what can be refunded.
REFUND_COUNTS = (
    RefundState.REQUESTED,
    RefundState.APPROVED,
    RefundState.EXECUTION_PENDING,
    RefundState.PROCESSOR_PENDING,
    RefundState.PARTIAL,
    RefundState.SUCCEEDED,
    RefundState.UNKNOWN,
)
CATEGORIES = ("DUPLICATE_PAYMENT", "CORRECTION", "CANCELLATION", "CONTRACT_RIGHT", "GOODWILL")
EVIDENCE_REQUIRED = frozenset({"CORRECTION", "DUPLICATE_PAYMENT", "CANCELLATION", "CONTRACT_RIGHT"})

# What each state may be called, and what it must never be claimed to be (the wireframe's exact language).
REFUND_WORDS: Mapping[RefundState, tuple[str, str]] = {
    RefundState.REQUESTED: (
        "Requested, awaiting finance review",
        "Refund initiated at the processor",
    ),
    RefundState.APPROVED: ("Approved, waiting for execution", "Refund completed"),
    RefundState.EXECUTION_PENDING: (
        "Sending to the processor; do not retry blindly",
        "Provider confirmed",
    ),
    RefundState.PROCESSOR_PENDING: (
        "The processor is processing it; last verified time shown",
        "Funds returned",
    ),
    RefundState.PARTIAL: ("Partially refunded", "Bank settled or ledger complete"),
    RefundState.SUCCEEDED: (
        "Provider confirmed; reconciliation still to come",
        "Bank settled or ledger complete",
    ),
    RefundState.FAILED: (
        "The processor failed it",
        "Safe to issue a second refund without looking",
    ),
    RefundState.UNKNOWN: (
        "Outcome unknown: a controlled investigation is open",
        "Safe to issue a second refund",
    ),
    RefundState.REJECTED: ("Rejected", "Refunded"),
    RefundState.EXPIRED: ("Expired before it was run", "Refunded"),
    RefundState.CANCELED: ("Withdrawn", "Refunded"),
}

_REFUND_NEXT: Mapping[RefundState, frozenset[RefundState]] = {
    RefundState.REQUESTED: frozenset(
        {RefundState.APPROVED, RefundState.REJECTED, RefundState.EXPIRED, RefundState.CANCELED}
    ),
    RefundState.APPROVED: frozenset(
        {RefundState.EXECUTION_PENDING, RefundState.EXPIRED, RefundState.CANCELED}
    ),
    RefundState.EXECUTION_PENDING: frozenset(
        {
            RefundState.PROCESSOR_PENDING,
            RefundState.SUCCEEDED,
            RefundState.PARTIAL,
            RefundState.FAILED,
            RefundState.UNKNOWN,
        }
    ),
    RefundState.PROCESSOR_PENDING: frozenset(
        {RefundState.SUCCEEDED, RefundState.PARTIAL, RefundState.FAILED, RefundState.UNKNOWN}
    ),
    RefundState.PARTIAL: frozenset(
        {RefundState.SUCCEEDED, RefundState.FAILED, RefundState.UNKNOWN}
    ),
    RefundState.UNKNOWN: frozenset(
        {
            RefundState.EXECUTION_PENDING,
            RefundState.PROCESSOR_PENDING,
            RefundState.SUCCEEDED,
            RefundState.PARTIAL,
            RefundState.FAILED,
        }
    ),
    RefundState.FAILED: frozenset(
        {RefundState.EXECUTION_PENDING}
    ),  # a new attempt, only after the processor was asked what it holds
}


def refund_transition(current: RefundState, target: RefundState) -> RefundState:
    if target is current:
        return current
    if target not in _REFUND_NEXT.get(current, frozenset()):
        raise OpsError("REFUND_INVALID_TRANSITION", f"{current.value} -> {target.value}")
    return target


def overpayment_minor(*, paid_minor: int, gross_minor: int, credited_minor: int) -> int:
    """What the customer paid beyond what the invoice still asks for after its credit notes. Never negative."""
    owed = max(gross_minor - credited_minor, 0)
    return max(paid_minor - owed, 0)


def refundable_minor(
    *, paid_minor: int, gross_minor: int, credited_minor: int, refunded_minor: int
) -> int:
    """What may still be refunded for one invoice: the overpayment, less refunds already made, in flight or awaiting execution. A refund and a credit note for
    the same money are therefore never both granted, and the total returned never exceeds the total paid."""
    return max(
        overpayment_minor(
            paid_minor=paid_minor, gross_minor=gross_minor, credited_minor=credited_minor
        )
        - refunded_minor,
        0,
    )


def check_refund_request(
    *,
    amount_minor: int,
    refundable: int,
    disputed: bool,
    category: str,
    evidence_ref: str | None,
    captured: bool,
) -> None:
    if category not in CATEGORIES:
        raise OpsError("CATEGORY_INVALID", category)
    if category in EVIDENCE_REQUIRED and not (evidence_ref or "").strip():
        raise OpsError(
            "EVIDENCE_REQUIRED",
            "a refund for a duplicate payment, a correction, a cancellation or a contract right names its evidence",
        )
    if not captured:
        raise OpsError("PAYMENT_NOT_CAPTURED", "only a captured payment is refunded")
    if disputed:
        raise OpsError(
            "REFUND_BLOCKED_BY_DISPUTE",
            "the payment is in dispute: a refund as well would return the money twice",
        )
    if amount_minor <= 0:
        raise OpsError("AMOUNT_INVALID", "an amount is more than zero")
    if amount_minor > refundable:
        raise OpsError(
            "REFUND_EXCEEDS_OVERPAYMENT",
            "a refund is limited to what the customer overpaid and has not already had refunded or credited",
        )


def provider_refund_outcome(
    status: str | None, provider_amount_minor: int | None, requested_minor: int
) -> tuple[RefundState, str | None]:
    """What the processor says, as a state. Only a recognised, completed answer is a success; anything else is pending, failed or UNKNOWN, never a guess."""
    s = (status or "").lower()
    if s == "succeeded":
        if provider_amount_minor is None:
            return RefundState.UNKNOWN, "AMOUNT_NOT_REPORTED"
        if provider_amount_minor == requested_minor:
            return RefundState.SUCCEEDED, None
        return (
            (RefundState.PARTIAL, None)
            if 0 < provider_amount_minor < requested_minor
            else (RefundState.UNKNOWN, "AMOUNT_MISMATCH")
        )
    if s in ("pending", "requires_action"):
        return RefundState.PROCESSOR_PENDING, None
    if s in ("failed", "canceled"):
        return RefundState.FAILED, f"PROVIDER_{s.upper()}"
    return RefundState.UNKNOWN, "UNRECOGNISED_PROVIDER_STATUS"


def may_send_again(state: RefundState, *, provider_holds_none: bool) -> bool:
    """A second attempt is sent only once the processor has been asked what it already holds for this refund and holds nothing. Never blindly."""
    return state in (RefundState.UNKNOWN, RefundState.FAILED) and provider_holds_none


# ----------------------------------------------------------------------------------------------------------------------------- disputes


class DisputeState(StrEnum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    DECISION_PENDING = "DECISION_PENDING"
    ACCEPTED = "ACCEPTED"
    CONTESTING = "CONTESTING"
    SUBMISSION_RECORDED = "SUBMISSION_RECORDED"
    SUBMISSION_VERIFIED = "SUBMISSION_VERIFIED"
    CLOSED_WON = "CLOSED_WON"
    CLOSED_LOST = "CLOSED_LOST"
    CLOSED_ACCEPTED = "CLOSED_ACCEPTED"
    CLOSED_OTHER = "CLOSED_OTHER"


DISPUTE_FINAL = frozenset(
    {
        DisputeState.CLOSED_WON,
        DisputeState.CLOSED_LOST,
        DisputeState.CLOSED_ACCEPTED,
        DisputeState.CLOSED_OTHER,
    }
)
EVIDENCE_ITEMS = (
    "terms_or_consent",
    "transaction_proof",
    "usage_proof",
    "communications",
    "cancellation_refund_chronology",
)

_DISPUTE_NEXT: Mapping[DisputeState, frozenset[DisputeState]] = {
    DisputeState.OPEN: frozenset({DisputeState.INVESTIGATING, DisputeState.DECISION_PENDING}),
    DisputeState.INVESTIGATING: frozenset({DisputeState.DECISION_PENDING}),
    DisputeState.DECISION_PENDING: frozenset({DisputeState.ACCEPTED, DisputeState.CONTESTING}),
    DisputeState.CONTESTING: frozenset({DisputeState.SUBMISSION_RECORDED}),
    DisputeState.SUBMISSION_RECORDED: frozenset(
        {DisputeState.SUBMISSION_VERIFIED, DisputeState.CONTESTING}
    ),
}


def dispute_transition(current: DisputeState, target: DisputeState) -> DisputeState:
    """A person's moves only. A close is the processor's (see `provider_dispute_close`) and is applied by the event that reports it, not by this."""
    if target is current:
        return current
    if current in DISPUTE_FINAL or target not in _DISPUTE_NEXT.get(current, frozenset()):
        raise OpsError("DISPUTE_INVALID_TRANSITION", f"{current.value} -> {target.value}")
    return target


def provider_dispute_close(status: str | None, *, accepted_by_us: bool) -> DisputeState | None:
    """The close that the processor reports. A manual verdict, or a checkbox that says "submitted", is never an outcome."""
    s = (status or "").lower()
    if s == "won":
        return DisputeState.CLOSED_WON
    if s == "lost":
        return DisputeState.CLOSED_ACCEPTED if accepted_by_us else DisputeState.CLOSED_LOST
    if s in ("warning_closed", "charge_refunded"):
        return DisputeState.CLOSED_OTHER
    return None


def deadline_view(due_by: datetime | None, now: datetime, *, zone: str = "UTC") -> dict[str, Any]:
    """The deadline exactly as the processor gave it, with its time zone, and how long is left. A missing deadline is said to be missing: no window is invented."""
    if due_by is None:
        return {
            "due_by": None,
            "zone": zone,
            "seconds_left": None,
            "status": "NO_DEADLINE_FROM_PROCESSOR",
        }
    left = int((due_by - now).total_seconds())
    status = "PASSED" if left <= 0 else "NEAR" if left <= 3 * 86_400 else "OK"
    return {
        "due_by": due_by.isoformat(),
        "zone": zone,
        "seconds_left": max(left, 0),
        "status": status,
    }


def checklist_complete(evidence: Mapping[str, Any]) -> bool:
    return all(
        isinstance(evidence.get(k), Mapping) and bool(evidence[k].get("ref"))
        for k in EVIDENCE_ITEMS
    )


# -------------------------------------------------------------------------------------------------------------------- reconciliation


class MatchState(StrEnum):
    UNMATCHED = "UNMATCHED"
    PROVISIONALLY_MATCHED = "PROVISIONALLY_MATCHED"
    MATCHED = "MATCHED"


@dataclass(frozen=True, slots=True)
class Item:
    """One thing the platform says happened with money: a captured payment, a refund the processor confirmed, or money a dispute withdrew."""

    kind: str  # PAYMENT | REFUND | DISPUTE
    item_id: str
    ref: str  # the processor reference the payout line is linked by
    amount_minor: int  # signed: a payment is positive, a refund or a dispute is negative
    currency: str
    issuing_entity: str
    occurred_on: date
    journal_ref: str = ""  # what the ledger journal may name instead (an invoice number)


@dataclass(frozen=True, slots=True)
class Leg:
    leg_type: str  # PAYOUT_LINE | BANK_LINE | GL_JOURNAL
    ref: str
    link_ref: str
    amount_minor: int
    currency: str


@dataclass(frozen=True, slots=True)
class Matched:
    item: Item
    state: MatchState
    reason: str | None
    difference_minor: int = 0
    payout_ref: str | None = None
    legs: tuple[str, ...] = field(default_factory=tuple)


def match_item(item: Item, legs: Sequence[Leg]) -> Matched:
    """Invoice, payment, refund or dispute to the processor's payout line, to the bank line for that payout, to the ledger journal. Each leg is its own fact; one
    missing leg is never filled in from another."""
    payout = [
        x
        for x in legs
        if x.leg_type == "PAYOUT_LINE" and x.link_ref == item.ref and x.currency == item.currency
    ]
    if not payout:
        return Matched(
            item, MatchState.UNMATCHED, "no processor payout line for it", item.amount_minor
        )
    total = sum(x.amount_minor for x in payout)
    if total != item.amount_minor:
        return Matched(
            item,
            MatchState.UNMATCHED,
            "the payout line differs from the platform's amount",
            total - item.amount_minor,
            payout[0].ref,
            ("PAYOUT_LINE",),
        )
    ref = payout[0].ref
    have = ["PAYOUT_LINE"]
    bank = [x for x in legs if x.leg_type == "BANK_LINE" and x.ref == ref]
    journal = [
        x
        for x in legs
        if x.leg_type == "GL_JOURNAL"
        and x.currency == item.currency
        and (
            x.ref == ref
            or (item.journal_ref and x.ref == item.journal_ref)
            or x.link_ref == item.ref
        )
    ]
    if bank:
        have.append("BANK_LINE")
    if journal:
        have.append("GL_JOURNAL")
    if not bank and not journal:
        return Matched(
            item,
            MatchState.PROVISIONALLY_MATCHED,
            "bank evidence and the ledger journal are outstanding",
            0,
            ref,
            tuple(have),
        )
    if not bank:
        return Matched(
            item,
            MatchState.PROVISIONALLY_MATCHED,
            "bank evidence is outstanding",
            0,
            ref,
            tuple(have),
        )
    if not journal:
        return Matched(
            item,
            MatchState.PROVISIONALLY_MATCHED,
            "the ledger journal is outstanding",
            0,
            ref,
            tuple(have),
        )
    return Matched(item, MatchState.MATCHED, None, 0, ref, tuple(have))


def board_hash(rows: Sequence[Matched]) -> str:
    """The digest a close signs: exactly what was reconciled, in a stable order. A change to any leg or any amount is another digest."""
    body = [
        [
            m.item.kind,
            m.item.item_id,
            m.item.ref,
            m.item.amount_minor,
            m.item.currency,
            m.state.value,
            m.difference_minor,
            m.payout_ref,
            list(m.legs),
        ]
        for m in sorted(rows, key=lambda r: (r.item.kind, r.item.item_id))
    ]
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def close_blockers(
    rows: Sequence[Matched],
    *,
    materiality_minor: int,
    open_refunds: int,
    open_disputes: int,
    resolved: frozenset[tuple[str, str]] = frozenset(),
) -> list[str]:
    """Why this period cannot close yet. An unmatched item shows however small; below the materiality line it does not stop a close by itself, but a missing
    leg always does. A refund or a dispute that is not final blocks, because its money is not settled."""
    out: list[str] = []
    for m in rows:
        key = (m.item.kind, m.item.item_id)
        if m.state is MatchState.MATCHED or key in resolved:
            continue
        if (
            m.state is MatchState.UNMATCHED
            and abs(m.difference_minor) < materiality_minor
            and m.payout_ref is not None
        ):
            continue  # a difference below the line, with its legs in place, is shown and does not stop the close
        out.append(f"{m.item.kind} {m.item.item_id}: {m.reason}")
    if open_refunds:
        out.append(f"{open_refunds} refund(s) are not final")
    if open_disputes:
        out.append(f"{open_disputes} dispute(s) are not final")
    return out


def check_close_signature(
    *,
    role: str,
    signer: str,
    allowed: Sequence[str],
    already: Mapping[str, str],
    presented: str,
    current: str,
    maker_roles: Sequence[str],
) -> None:
    """Whether this person may sign this close now: the roles the policy names, exactly what was reconciled, and never one person in the maker's and the checker's place."""
    if presented != current:
        raise OpsError(
            "SNAPSHOT_CHANGED", "what was reconciled changed since it was read; read it again"
        )
    if role not in allowed:
        raise OpsError("ROLE_NOT_ALLOWED", f"{role} does not sign this")
    if role in already:
        raise OpsError("ROLE_ALREADY_SIGNED", f"{role} has already signed")
    if signer in already.values():
        raise OpsError(
            "ONE_PERSON_TWO_SLOTS", "one person cannot be both the maker and the checker"
        )
    _ = maker_roles
