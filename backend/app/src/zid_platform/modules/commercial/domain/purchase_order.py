"""Purchase-order rules (ZID-COM-WF-2026-11 CW-11). Pure.

A company that has purchase orders on record is invoiced only against one: same currency, inside its validity, with enough value left. Of the POs that fit, the one that
expires soonest is used first. If none fits the invoice is not issued, so a missing or used-up PO is found before the invoice exists, never after. A company with no PO
on record is not covered by this rule at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


class PurchaseOrderError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class PO:
    po_id: str
    number: str
    currency: str
    value: Decimal
    drawn: Decimal
    valid_from: date
    valid_until: date
    status: str

    @property
    def remaining(self) -> Decimal:
        return self.value - self.drawn


def choose(pos: list[PO], *, currency: str, amount: Decimal, on: date) -> PO | None:
    """The PO to draw an invoice against, or None when the company has no POs on record (the rule does not apply). Raises when it has some and none fits."""
    if not pos:
        return None
    fits = [
        p
        for p in pos
        if p.status == "ACTIVE"
        and p.currency == currency
        and p.valid_from <= on <= p.valid_until
        and p.remaining >= amount
    ]
    if fits:
        return min(fits, key=lambda p: (p.valid_until, p.number))
    live = [
        p
        for p in pos
        if p.status == "ACTIVE" and p.currency == currency and p.valid_from <= on <= p.valid_until
    ]
    if live:
        raise PurchaseOrderError(
            "PO_INSUFFICIENT", f"{max(p.remaining for p in live)} left on the best open PO"
        )
    raise PurchaseOrderError("PO_REQUIRED", "no open purchase order in this currency covers today")
