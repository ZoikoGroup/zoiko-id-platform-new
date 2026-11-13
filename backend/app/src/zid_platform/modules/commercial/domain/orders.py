"""Quotes, discount authority and orders (ZID-COM-001 §10, §12, §14, §27, §31; CR-028). Pure domain.

A quote is validated against the catalogue and the commercial rules before anyone may accept it. Every
failure is reported with its own reason code, not just the first. Hard failures reject the quote; rules that
need a person's judgement (vendor cost, large discounts) make it NEEDS_APPROVAL. A signed order is never
repriced by a later price book.

The discount bands are data (`commercial.discount_policy`), seeded from the proposal: 0-5% the account
executive, over 5-12% the CCO, over 12% the CFO and the Founder; an annual prepaid discount is capped at 10%;
a third-party pass-through line is never discounted.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

HARD = "HARD"
APPROVAL = "APPROVAL"

# reason code -> severity
REASONS = {
    "SKU_UNKNOWN": HARD,
    "SKU_NOT_RELEASED": HARD,
    "REGION_NOT_RELEASED": HARD,
    "PRICE_BOOK_NOT_APPROVED": HARD,
    "PRICE_BOOK_NOT_EFFECTIVE": HARD,
    "PRICE_BOOK_CURRENCY_MISMATCH": HARD,  # a book in one currency never prices an account that is billed in another (GLB-35)
    "PRICE_MISSING": HARD,
    "PASSTHROUGH_DISCOUNT": HARD,
    "PREPAID_DISCOUNT_CEILING": HARD,
    "ADDON_WITHOUT_PARENT": HARD,
    "PLAN_LINE_MISSING": HARD,
    "UNCAPPED_LIABILITY": HARD,
    "RESIDENCY_OUTSIDE_RELEASE": HARD,
    "DISCOUNT_INVALID": HARD,
    "BELOW_FLOOR": APPROVAL,
    "VENDOR_COST_UNCONFIRMED": APPROVAL,
    "DISCOUNT_NEEDS_APPROVAL": APPROVAL,
}

PREPAID_CEILING = Decimal(
    "0.10"
)  # §12: annual prepaid discount ceiling 10%, subject to the margin floor


class OrderError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code, self.detail = code, detail


@dataclass(frozen=True, slots=True)
class Band:
    """Discounts up to and including `max_pct` (None: no upper bound) need all of `approvers`."""

    max_pct: Decimal | None
    approvers: frozenset[str]


@dataclass(frozen=True, slots=True)
class QuoteLine:
    seq: int
    sku: str
    plan_code: str
    quantity: Decimal
    discount_pct: Decimal = Decimal(0)
    promotions: tuple[
        Decimal, ...
    ] = ()  # stacked on top of the quoted discount; judged together with it


def effective_discount(ln: QuoteLine) -> Decimal:
    """The discount a customer actually gets from the quoted discount and every promotion on the line: 1 - product of (1 - each). Approval authority
    follows this, so splitting one concession into several promotions never moves it into a band that approves less."""
    keep = Decimal(1) - ln.discount_pct
    for p in ln.promotions:
        keep *= Decimal(1) - p
    return Decimal(1) - keep


@dataclass(frozen=True, slots=True)
class Catalogue:
    """What the validator needs to know, read from the database by the caller."""

    released_skus: frozenset[str]
    sku_consumption: Mapping[str, str]  # sku -> FLAT | METERED | PASSTHROUGH | CONTRACT
    prices: Mapping[tuple[str, str], Decimal]  # (sku, plan) -> list unit price in the quoted book
    released_regions: frozenset[str]
    floors: Mapping[tuple[str, str], Decimal] = field(
        default_factory=dict
    )  # (sku, region) -> floor price
    confirmed_vendor_skus: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Quote:
    lines: tuple[QuoteLine, ...]
    region: str
    currency: str
    price_book_status: str
    price_book_effective_from: datetime | None
    quoted_at: datetime
    prepaid: bool = False
    prepaid_discount_pct: Decimal = Decimal(0)
    liability_capped: bool = True
    residency_commitment: str | None = None
    account_currency: str | None = (
        None  # what the account is billed in; None means the caller did not say
    )


@dataclass(frozen=True, slots=True)
class Validation:
    status: str  # VALIDATED | NEEDS_APPROVAL | REJECTED
    reasons: tuple[str, ...]
    approvers_required: frozenset[str]
    lines: tuple[dict[str, str], ...]

    def view(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reasons": list(self.reasons),
            "approvers_required": sorted(self.approvers_required),
            "lines": list(self.lines),
        }


def approvers_for(discount: Decimal, bands: Sequence[Band]) -> frozenset[str]:
    """The roles that must approve a discount of `discount` (a fraction, 0.07 = 7%)."""
    if discount <= 0:
        return frozenset()
    for band in sorted(bands, key=lambda x: (x.max_pct is None, x.max_pct or Decimal(0))):
        if band.max_pct is None or discount <= band.max_pct:
            return band.approvers
    raise OrderError("DISCOUNT_POLICY_INCOMPLETE", "no band covers this discount")


def _money(v: Decimal) -> str:
    return format(v.quantize(Decimal("0.000001")).normalize(), "f")


def validate(quote: Quote, cat: Catalogue, bands: Sequence[Band]) -> Validation:
    reasons: list[str] = []
    approvers: set[str] = set()
    priced: list[dict[str, Any]] = []

    def add(code: str) -> None:
        if code not in reasons:
            reasons.append(code)

    if quote.account_currency is not None and quote.account_currency != quote.currency:
        add(
            "PRICE_BOOK_CURRENCY_MISMATCH"
        )  # no conversion is ever assumed: a missing signed book in the account currency blocks
    if quote.price_book_status != "APPROVED":
        add("PRICE_BOOK_NOT_APPROVED")
    elif (
        quote.price_book_effective_from is None or quote.price_book_effective_from > quote.quoted_at
    ):
        add("PRICE_BOOK_NOT_EFFECTIVE")
    if quote.region not in cat.released_regions:
        add(
            "REGION_NOT_RELEASED"
        )  # an SLA or deployment for an unlaunched cell cannot be promised (App. C)
    if (
        quote.residency_commitment is not None
        and quote.residency_commitment not in cat.released_regions
    ):
        add("RESIDENCY_OUTSIDE_RELEASE")
    if not quote.liability_capped:
        add("UNCAPPED_LIABILITY")
    if quote.prepaid and quote.prepaid_discount_pct > PREPAID_CEILING:
        add("PREPAID_DISCOUNT_CEILING")
    elif quote.prepaid and quote.prepaid_discount_pct > 0:
        approvers |= {"CFO"}

    plans = {ln.plan_code for ln in quote.lines if ln.sku == "ZID-BASE"}
    if not plans:
        add("PLAN_LINE_MISSING")  # add-ons are sold only on top of a plan (§6)
    max_discount = Decimal(0)
    for ln in quote.lines:
        if not Decimal(0) <= ln.discount_pct < 1 or any(
            not Decimal(0) <= p < 1 for p in ln.promotions
        ):
            add("DISCOUNT_INVALID")
            continue
        eff = effective_discount(ln)
        consumption = cat.sku_consumption.get(ln.sku)
        if consumption is None:
            add("SKU_UNKNOWN")
            continue
        if ln.sku not in cat.released_skus:
            add("SKU_NOT_RELEASED")
        if ln.sku != "ZID-BASE" and ln.plan_code not in plans:
            add("ADDON_WITHOUT_PARENT")
        if consumption == "PASSTHROUGH":
            if eff > 0:
                add(
                    "PASSTHROUGH_DISCOUNT"
                )  # §12: no unallocated discount on third-party pass-through
            if ln.sku not in cat.confirmed_vendor_skus:
                add("VENDOR_COST_UNCONFIRMED")
            continue
        price = cat.prices.get((ln.sku, ln.plan_code))
        if price is None:
            if consumption != "CONTRACT":
                add("PRICE_MISSING")
            continue
        net = price * (1 - eff)
        floor = cat.floors.get((ln.sku, quote.region))
        if floor is not None and net < floor:
            add("BELOW_FLOOR")
            approvers |= {
                "CFO"
            }  # §12: the system rejects a quote below the floor without CFO risk acceptance
        max_discount = max(max_discount, eff)
        priced.append(
            {
                "seq": str(ln.seq),
                "sku": ln.sku,
                "plan": ln.plan_code,
                "quantity": _money(ln.quantity),
                "list_unit_price": _money(price),
                "discount_pct": _money(ln.discount_pct),
                "promotions": [_money(x) for x in ln.promotions],
                "effective_discount_pct": _money(eff),
                "net_unit_price": _money(net),
            }
        )
    needed = approvers_for(max_discount, bands)
    if needed:
        approvers |= needed
        add("DISCOUNT_NEEDS_APPROVAL")
    if any(REASONS[r] == HARD for r in reasons):
        status = "REJECTED"
    elif reasons or approvers:
        status = "NEEDS_APPROVAL"
    else:
        status = "VALIDATED"
    return Validation(status, tuple(reasons), frozenset(approvers), tuple(priced))


def digest(payload: Mapping[str, object]) -> str:
    """The quote's identity for approvals: an approval binds this exact digest, so any change voids it."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def missing_approvals(
    required: frozenset[str], given: Mapping[str, str], creator: str
) -> frozenset[str]:
    """Roles still to approve. `given` maps role -> approver principal; the creator never counts and one
    person never fills two roles."""
    seen: set[str] = set()
    satisfied: set[str] = set()
    for role, who in sorted(given.items()):
        if who == creator or who in seen:
            continue
        seen.add(who)
        satisfied.add(role)
    return frozenset(required - satisfied)
