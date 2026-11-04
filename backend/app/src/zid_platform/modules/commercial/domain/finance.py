"""Who may approve a money instrument, and how much (ZID-COM-CL-004, ZID-COM-RAT-011; slice L-14b). Pure: no I/O.

A credit note or a pilot credit is placed in a tier by its exposure in USD minor units (rounded up, never down, so a request is never
put in a lower tier by rounding), and each tier names the roles that must ALL approve, each a different person. Credits for one organization within the
policy's window add up, so a credit cannot be split to stay under a limit. The person who asked never approves their own request. Money is a
whole number of minor units or a Decimal; there are no floats.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, Decimal
from enum import StrEnum
from typing import Any


class FinanceError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail or code


class Kind(StrEnum):
    CREDIT_NOTE = "CREDIT_NOTE"
    PILOT_CREDIT = "PILOT_CREDIT"


class State(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    APPLYING = "APPLYING"
    APPLIED = "APPLIED"
    APPLICATION_FAILED = "APPLICATION_FAILED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELED = "CANCELED"


PAID_DISPOSITIONS = (
    "REFUND",
    "CUSTOMER_BALANCE",
    "OUT_OF_BAND",
)  # what becomes of money already taken on a credited invoice
FINAL = frozenset({State.APPLIED, State.REJECTED, State.EXPIRED, State.CANCELED})
# the exposure of a new request adds what these already hold for the same organization in the window
COUNTS_TOWARDS_EXPOSURE = (State.PENDING_APPROVAL, State.APPROVED, State.APPLYING, State.APPLIED)


@dataclass(frozen=True)
class Tier:
    max_minor: int | None  # USD minor units, inclusive; None is every amount above the last limit
    roles: tuple[str, ...]


@dataclass(frozen=True)
class Instrument:
    window_days: int
    tiers: tuple[Tier, ...]
    max_days: int | None = None


@dataclass(frozen=True)
class CommitmentPolicy:
    valid_days: int
    tier1_max_minor: int  # USD minor units, inclusive
    tier2_max_minor: int
    overlay_term_months: int
    enforcement: str  # OFF | REPORT_ONLY | ENFORCED


@dataclass(frozen=True)
class DisputePolicy:
    """Who owns a dispute, who may accept or contest it (a finance decision), who may hand it to the processor (a separate appointment)."""

    owner_roles: tuple[str, ...]
    decide_roles: tuple[str, ...]
    submit_roles: tuple[str, ...]


@dataclass(frozen=True)
class ReconPolicy:
    """Who closes a period (a maker and a checker, different people), who reopens one (two different people, neither the one who asked), and the amount in USD
    minor units below which an unmatched difference is not material enough to block a close."""

    close_maker_roles: tuple[str, ...]
    close_checker_roles: tuple[str, ...]
    reopen_roles: tuple[str, ...]
    materiality_minor: int


@dataclass(frozen=True)
class FinancePolicy:
    version: str
    content_hash: str
    fx_usd: Mapping[str, Decimal]
    request_roles: tuple[str, ...]
    execute_roles: tuple[str, ...]
    request_valid_days: int
    approval_valid_days: int
    credit_note: Instrument
    pilot_credit: Instrument
    commitment: CommitmentPolicy
    refund: Instrument | None = (
        None  # no refund tiers in the signed policy means no refund can be requested or approved
    )
    dispute: DisputePolicy | None = None
    recon: ReconPolicy | None = None

    def instrument(self, kind: Kind) -> Instrument:
        return self.credit_note if kind is Kind.CREDIT_NOTE else self.pilot_credit


def to_minor(amount: Decimal) -> int:
    """A money amount as whole minor units. More than two decimals is refused rather than rounded: nobody is charged or credited a fraction."""
    if amount <= 0:
        raise FinanceError("AMOUNT_INVALID", "an amount is more than zero")
    cents = amount * 100
    if cents != cents.to_integral_value():
        raise FinanceError("AMOUNT_INVALID", "an amount has at most two decimals")
    return int(cents)


def usd_minor(amount_minor: int, currency: str, fx_usd: Mapping[str, Decimal]) -> int:
    """The amount in USD minor units at the pinned rate, rounded UP. A currency with no pinned rate is refused, never guessed."""
    rate = fx_usd.get(currency)
    if rate is None:
        raise FinanceError("FX_UNPINNED", f"the policy pins no USD rate for {currency}")
    return int((Decimal(amount_minor) * rate).to_integral_value(rounding=ROUND_CEILING))


def tier_for(instrument: Instrument, exposure_usd_minor: int) -> Tier:
    for tier in instrument.tiers:
        if tier.max_minor is None or exposure_usd_minor <= tier.max_minor:
            return tier
    raise FinanceError(
        "TIER_MISSING", "the policy has no tier for this amount"
    )  # the last tier has no limit, so this is a broken policy


def validate_policy(p: FinancePolicy) -> None:
    """A policy that cannot place every amount, names a role twice in a tier (one person in two slots is the thing it exists to stop), or lets
    a lower limit follow a higher one does not load."""
    instruments = [("credit_note", p.credit_note), ("pilot_credit", p.pilot_credit)]
    if p.refund is not None:
        instruments.append(("refund", p.refund))
    for name, inst in instruments:
        if not inst.tiers or inst.tiers[-1].max_minor is not None:
            raise FinanceError("POLICY_INVALID", f"{name}: the last tier must have no limit")
        last = 0
        for t in inst.tiers[:-1]:
            if t.max_minor is None or t.max_minor <= last:
                raise FinanceError("POLICY_INVALID", f"{name}: limits must rise")
            last = t.max_minor
        for t in inst.tiers:
            if not t.roles or len(set(t.roles)) != len(t.roles):
                raise FinanceError("POLICY_INVALID", f"{name}: a tier names each role once")
    c = p.commitment
    if (
        not 0 < c.tier1_max_minor < c.tier2_max_minor
        or c.valid_days < 1
        or c.overlay_term_months < 12
    ):
        raise FinanceError("POLICY_INVALID", "commitment limits must rise and be positive")
    if c.enforcement not in ("OFF", "REPORT_ONLY", "ENFORCED"):
        raise FinanceError(
            "POLICY_INVALID", "commitment enforcement is OFF, REPORT_ONLY or ENFORCED"
        )
    if p.dispute is not None and not (
        p.dispute.owner_roles and p.dispute.decide_roles and p.dispute.submit_roles
    ):
        raise FinanceError(
            "POLICY_INVALID",
            "a dispute names who owns it, who decides it and who hands it to the processor",
        )
    if p.dispute is not None and set(p.dispute.decide_roles) & set(p.dispute.submit_roles):
        raise FinanceError(
            "POLICY_INVALID", "deciding a dispute and submitting it are different permissions"
        )
    if p.recon is not None:
        r = p.recon
        if (
            not (r.close_maker_roles and r.close_checker_roles and len(set(r.reopen_roles)) >= 2)
            or r.materiality_minor < 0
        ):
            raise FinanceError(
                "POLICY_INVALID",
                "a close names a maker and a checker, and a reopen names at least two roles",
            )
        if set(r.close_maker_roles) & set(r.close_checker_roles):
            raise FinanceError(
                "POLICY_INVALID", "the maker and the checker of a close are different roles"
            )
    if not p.request_roles or not p.execute_roles:
        raise FinanceError("POLICY_INVALID", "who may open and who may run must be named")
    if set(p.request_roles) & set(p.execute_roles):
        raise FinanceError("POLICY_INVALID", "opening and running are different permissions")


def snapshot_hash(snapshot: Mapping[str, Any]) -> str:
    """The digest an approval is bound to: canonical JSON of exactly the facts approved. Any change to them is another digest."""
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def check_decision(
    *,
    required: tuple[str, ...],
    role: str,
    approver: str,
    requester: str,
    decided: Mapping[str, str],
    presented_hash: str,
    request_hash: str,
) -> None:
    """Whether this person may sign this role of this request now. `decided` is role -> principal for what is already signed."""
    if presented_hash != request_hash:
        raise FinanceError(
            "SNAPSHOT_CHANGED", "the request changed since it was read; read it again"
        )
    if role not in required:
        raise FinanceError("ROLE_NOT_REQUIRED", f"{role} is not asked to sign this request")
    if role in decided:
        raise FinanceError("ROLE_ALREADY_DECIDED", f"{role} has already signed")
    if approver == requester:
        raise FinanceError("SELF_APPROVAL", "the person who asked never signs their own request")
    if approver in decided.values():
        raise FinanceError(
            "ONE_PERSON_TWO_SLOTS", "one person cannot sign two roles of the same request"
        )


def is_complete(required: tuple[str, ...], decided: Mapping[str, str]) -> bool:
    return all(r in decided for r in required)


def check_execution(
    *,
    state: State,
    approval_expires_at: datetime | None,
    now: datetime,
    executor: str,
    requester: str,
    approvers: tuple[str, ...],
) -> None:
    if state is not State.APPROVED:
        raise FinanceError("NOT_APPROVED", f"a request that is {state.value} is not run")
    if approval_expires_at is None or approval_expires_at <= now:
        raise FinanceError("APPROVAL_EXPIRED", "the approval lapsed before the request was run")
    if executor == requester or executor in approvers:
        raise FinanceError(
            "EXECUTOR_NOT_INDEPENDENT", "whoever runs it neither asked for it nor signed it"
        )
