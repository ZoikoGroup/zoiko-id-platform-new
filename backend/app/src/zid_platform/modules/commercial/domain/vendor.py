"""Vendor costs for third-party checks (ZID-COM-001 §7, §8, §23, §31; CR-029). Pure domain.

Identity verification, KYB, risk screening and messaging are supplied by vendors. Every attempt has a cost,
recorded whether or not the customer is charged, so margin is honest:

- a provider error (5xx) or a Zoiko technical error is never charged to the customer (§7);
- a customer-initiated retry or an insufficient-data attempt is chargeable at the check's retry rate;
- a completed check is chargeable at the base rate on the first attempt and the retry rate after.

Only a SIGNED_TARIFF rate confirms a route for quoting; a QUOTED or ESTIMATE rate never backs a price.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

OUTCOMES = ("COMPLETED", "INSUFFICIENT_DATA", "CUSTOMER_RETRY", "PROVIDER_ERROR", "PLATFORM_ERROR")
NOT_CHARGEABLE = frozenset({"PROVIDER_ERROR", "PLATFORM_ERROR"})
CONFIDENCE = ("SIGNED_TARIFF", "QUOTED", "ESTIMATE")


class VendorError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code, self.detail = code, detail


@dataclass(frozen=True, slots=True)
class Rate:
    base_price: Decimal
    retry_price: Decimal
    currency: str
    confidence: str


@dataclass(frozen=True, slots=True)
class Attempt:
    """What one attempt costs Zoiko, and whether and at which price the customer may be charged for it."""

    outcome: str
    attempt_no: int
    cost: Decimal
    chargeable: bool
    charge_basis: str  # BASE | RETRY | NONE


def classify(decision_status: str, reasons: Iterable[str]) -> str:
    """Map a verification decision to a billing outcome. Anything that is the provider's or our fault is
    never chargeable."""
    rs = set(reasons)
    if "PROVIDER_UNAVAILABLE" in rs or "PROVIDER_ERROR" in rs:
        return "PROVIDER_ERROR"
    if decision_status == "FAILED" and rs & {
        "INSUFFICIENT_DATA",
        "DOCUMENT_UNREADABLE",
        "EVIDENCE_INCOMPLETE",
    }:
        return "INSUFFICIENT_DATA"
    return "COMPLETED"


def price_attempt(rate: Rate, outcome: str, attempt_no: int) -> Attempt:
    if outcome not in OUTCOMES:
        raise VendorError("OUTCOME_UNKNOWN", outcome)
    if attempt_no < 1:
        raise VendorError("ATTEMPT_INVALID")
    retry = attempt_no > 1 or outcome in ("CUSTOMER_RETRY", "INSUFFICIENT_DATA")
    cost = rate.retry_price if retry else rate.base_price
    if outcome in NOT_CHARGEABLE:
        return Attempt(outcome, attempt_no, cost, False, "NONE")
    return Attempt(outcome, attempt_no, cost, True, "RETRY" if retry else "BASE")


def check_rate(base: Decimal, retry: Decimal, confidence: str) -> None:
    if base < 0 or retry < 0:
        raise VendorError("RATE_INVALID", "a supplier price is never negative")
    if confidence not in CONFIDENCE:
        raise VendorError("CONFIDENCE_UNKNOWN")


def is_spike(previous: Decimal | None, new: Decimal, threshold_pct: Decimal | None) -> bool:
    """True when a new base price exceeds the previous one by more than the vendor's configured threshold.
    With no threshold configured there is no automatic spike rule."""
    if previous is None or threshold_pct is None or previous <= 0:
        return False
    return (new - previous) * 100 / previous > threshold_pct


@dataclass(frozen=True, slots=True)
class Reconciliation:
    matched: tuple[str, ...]
    amount_mismatch: tuple[tuple[str, Decimal, Decimal], ...]  # (supplier ref, our cost, invoiced)
    missing_on_invoice: tuple[str, ...]  # we recorded a check the supplier did not bill
    unknown_on_invoice: tuple[str, ...]  # the supplier billed a check we have no record of
    duplicate_on_invoice: tuple[str, ...]


def reconcile(
    costs: Mapping[str, Decimal], invoice: Sequence[tuple[str, Decimal]]
) -> Reconciliation:
    """Join supplier invoice lines to our cost events by supplier reference."""
    seen: dict[str, Decimal] = {}
    dupes: list[str] = []
    for ref, amount in invoice:
        if ref in seen:
            dupes.append(ref)
            continue
        seen[ref] = amount
    matched, mismatch = [], []
    for ref, amount in seen.items():
        if ref in costs:
            if costs[ref] == amount:
                matched.append(ref)
            else:
                mismatch.append((ref, costs[ref], amount))
    return Reconciliation(
        tuple(sorted(matched)),
        tuple(sorted(mismatch)),
        tuple(sorted(set(costs) - set(seen))),
        tuple(sorted(set(seen) - set(costs))),
        tuple(sorted(dupes)),
    )


def margin(
    billed: Decimal,
    vendor_cost: Decimal,
    processing: Decimal = Decimal(0),
    review: Decimal = Decimal(0),
) -> Decimal | None:
    """§8: (billed check revenue - vendor cost - processing - manual review) / billed. None when nothing was billed."""
    if billed <= 0:
        return None
    return ((billed - vendor_cost - processing - review) / billed).quantize(Decimal("0.0001"))
