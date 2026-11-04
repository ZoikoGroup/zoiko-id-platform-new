"""Customer-side commitment approval (ZID-COM-CL-004 section 04; slice L-14c, CR-054). Pure: no I/O.

A commitment is a NEW or INCREASED contractual obligation of a customer organization. Its exposure is the higher of the first-12-month committed value
and the whole non-cancelable term value (so a recurring +$400 a month for 12 months is one $4,800 approval, not twelve). The tier says which authority may
approve: the Owner, a Billing Admin the Owner gave a spend limit, or a Procurement Approver the Owner named with a limit; above the second tier it takes
two different people. Nobody approves twice, a delegate never beyond their limit, and a delegate who raised a request above the first tier does not approve it (a Billing Admin may raise and approve within their own limit).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from zid_platform.modules.commercial.domain.finance import CommitmentPolicy, FinanceError


class CommitmentError(FinanceError):
    """The same shape as a finance refusal, so routes map one error type."""


class Tier(StrEnum):
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"


class Authority(StrEnum):
    OWNER = "OWNER"
    BILLING_SPEND = "BILLING_SPEND"
    PROCUREMENT_APPROVER = "PROCUREMENT_APPROVER"


DELEGATE_KINDS = (Authority.BILLING_SPEND.value, Authority.PROCUREMENT_APPROVER.value)
LIVE = ("PENDING_APPROVAL", "APPROVED")


def exposure_minor(monthly_minor: int, term_months: int, amended_monthly_minor: int = 0) -> int:
    """Incremental exposure in the contract's minor units: the higher of the first 12 months and the whole term, less what the amended order already
    committed. Never below zero (a smaller commitment adds no exposure)."""
    if monthly_minor < 0 or term_months < 1:
        raise CommitmentError(
            "EXPOSURE_INVALID",
            "a commitment has a non-negative monthly value and at least one month",
        )
    first_year = monthly_minor * min(12, term_months)
    whole = monthly_minor * term_months
    before = amended_monthly_minor * min(12, term_months)
    return max(0, max(first_year, whole) - before)


def tier_for(policy: CommitmentPolicy, exposure_usd_minor: int) -> Tier:
    if exposure_usd_minor <= policy.tier1_max_minor:
        return Tier.T1
    return Tier.T2 if exposure_usd_minor <= policy.tier2_max_minor else Tier.T3


def needs_review(
    policy: CommitmentPolicy,
    *,
    liability_capped: bool,
    residency_commitment: str | None,
    term_months: int,
) -> bool:
    """Nonstandard terms go to Contract/Legal/Finance whatever the amount: they are never auto-executed because the number is small."""
    return (
        (not liability_capped)
        or residency_commitment is not None
        or term_months > policy.overlay_term_months
    )


@dataclass(frozen=True)
class Delegate:
    kind: str
    cap_usd_minor: int


def authority_of(
    tier: Tier,
    *,
    is_owner: bool,
    delegate: Delegate | None,
    exposure_usd_minor: int,
    already: Mapping[str, str],
) -> Authority:
    """The authority this person signs in, or a refusal. The Owner may sign any tier (the second signature of the top tier is a Procurement Approver's)."""
    if is_owner and Authority.OWNER.value not in already.values():
        return Authority.OWNER
    wanted = (
        Authority.BILLING_SPEND.value if tier is Tier.T1 else Authority.PROCUREMENT_APPROVER.value
    )
    if delegate is None or delegate.kind != wanted:
        raise CommitmentError(
            "NOT_AN_APPROVER", "you are not the Owner or a delegate with authority for this amount"
        )
    if delegate.cap_usd_minor < exposure_usd_minor:
        raise CommitmentError(
            "OVER_SPEND_LIMIT", "the amount is above the limit the Owner gave you"
        )
    return Authority(wanted)


def check_decision(
    tier: Tier,
    *,
    approver: str,
    requester: str,
    is_owner: bool,
    delegate: Delegate | None,
    exposure_usd_minor: int,
    decided: Mapping[str, str],  # principal -> authority already signed
    presented_hash: str,
    request_hash: str,
    review_required: bool,
    reviewed: bool,
) -> Authority:
    if presented_hash != request_hash:
        raise CommitmentError(
            "SNAPSHOT_CHANGED", "the request changed since it was read; read it again"
        )
    if review_required and not reviewed:
        raise CommitmentError(
            "REVIEW_REQUIRED",
            "nonstandard terms need Zoiko contract, legal and finance review before any approval counts",
        )
    if approver in decided:
        raise CommitmentError("ALREADY_SIGNED", "you have already signed this request")
    authority = authority_of(
        tier,
        is_owner=is_owner,
        delegate=delegate,
        exposure_usd_minor=exposure_usd_minor,
        already=decided,
    )
    if approver == requester and not is_owner and tier is not Tier.T1:
        raise CommitmentError(
            "SELF_APPROVAL",
            "above the first tier, the person who asked for a commitment does not approve it unless they are the Owner",
        )
    if authority.value in decided.values():
        raise CommitmentError(
            "AUTHORITY_ALREADY_GIVEN", f"the {authority.value} signature is already given"
        )
    return authority


def is_complete(tier: Tier, decided: Mapping[str, str]) -> bool:
    signed = set(decided.values())
    if tier is Tier.T3:
        return {Authority.OWNER.value, Authority.PROCUREMENT_APPROVER.value} <= signed and len(
            decided
        ) >= 2
    return bool(signed)


def check_delegation(
    kind: str, cap_usd_minor: int, *, grantor: str, grantee: str, is_owner: bool
) -> None:
    if not is_owner:
        raise CommitmentError(
            "OWNER_ONLY", "only the Owner gives or takes away a spend limit or a procurement role"
        )
    if grantor == grantee:
        raise CommitmentError("SELF_DELEGATION", "nobody gives themselves a limit")
    if kind not in DELEGATE_KINDS:
        raise CommitmentError("KIND_UNKNOWN", kind)
    if cap_usd_minor <= 0:
        raise CommitmentError("CAP_INVALID", "a limit is more than zero")
