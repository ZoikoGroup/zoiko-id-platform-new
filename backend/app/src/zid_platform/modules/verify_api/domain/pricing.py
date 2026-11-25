"""The price of a Verify API usage fact, and what a month of usage would cost at it (ZID-EXT-IDV-001 section 12, WF-15, decision D12; CR-161). Pure rules, no I/O.

- A price is for ONE usage fact (a production session started, a check completed), in ONE currency, from the FIRST DAY of a month. Prices run by whole month, so a month is never part one price and part another.
- An operator proposes a price and a DIFFERENT operator approves it. An approved price is never edited: a correction is a new price from a later month.
- A price cannot start today or in the past: it takes effect from a month that has not begun, so nothing already used is silently repriced.
- Only currencies the commercial estate has declared minor-unit rules for are accepted; money is Decimal, never a float, and is rounded once per line.
- The cost shown is INDICATIVE: a count times a rate. It is not an invoice, includes no tax, and states the price it used. A meter with usage but no price makes that currency's total incomplete, and says so instead of showing a smaller number.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any

from zid_platform.modules.verify_api.domain.model import PartnerError

METERS: dict[str, str] = {
    "VERIFY_SESSION_START": "A production verification started",
    "VERIFY_CHECK_COMPLETE": "A production check completed with a final result",
}
MAX_RATE = Decimal(100000)
RATE_PLACES = Decimal("0.000001")
NOTE_MAX = 1000


class PriceStatus:
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


def first_of_next_month(today: date) -> date:
    return date(today.year + (today.month == 12), today.month % 12 + 1, 1)


def check_price(
    body: Mapping[str, Any], *, today: date, minor_units: Mapping[str, int]
) -> dict[str, Any]:
    """The cleaned proposal, or the first thing wrong with it."""
    meter = body.get("meter")
    if meter not in METERS:
        raise PartnerError("VALIDATION_FAILED", "Choose a usage fact: " + ", ".join(METERS) + ".")
    currency = body.get("currency")
    if not isinstance(currency, str) or currency not in minor_units:
        raise PartnerError(
            "VALIDATION_FAILED",
            "Choose a currency with a declared minor-unit rule: "
            + ", ".join(sorted(minor_units))
            + ".",
        )
    raw = body.get("unit_rate")
    if isinstance(raw, (bool, float)) or raw is None:
        raise PartnerError(
            "VALIDATION_FAILED", "Give the price as a decimal string such as 1.25, never a float."
        )
    try:
        rate = Decimal(str(raw))
    except InvalidOperation as e:
        raise PartnerError("VALIDATION_FAILED", "The price is not a number.") from e
    if not rate.is_finite() or rate < 0 or rate > MAX_RATE:
        raise PartnerError("VALIDATION_FAILED", f"The price is between 0 and {MAX_RATE}.")
    if rate != rate.quantize(RATE_PLACES, rounding=ROUND_HALF_EVEN):
        raise PartnerError("VALIDATION_FAILED", "The price has at most 6 decimal places.")
    start = body.get("effective_from")
    try:
        when = start if isinstance(start, date) else date.fromisoformat(str(start))
    except ValueError as e:
        raise PartnerError(
            "VALIDATION_FAILED", "Give the first day of a month as a date, for example 2027-01-01."
        ) from e
    if when.day != 1:
        raise PartnerError("VALIDATION_FAILED", "A price starts on the first day of a month.")
    if when < first_of_next_month(today):
        raise PartnerError(
            "PRICE_NOT_IN_FUTURE",
            "A price cannot start this month or earlier, so nothing already used is repriced. Choose a later month.",
        )
    return {"meter": str(meter), "currency": currency, "unit_rate": rate, "effective_from": when}


def check_note(note: str | None, *, required: bool) -> str:
    clean = " ".join((note or "").split())
    if len(clean) > NOTE_MAX:
        raise PartnerError("VALIDATION_FAILED", f"The note is longer than {NOTE_MAX} characters.")
    if required and len(clean) < 3:
        raise PartnerError("NOTE_REQUIRED", "Give a reason.")
    return clean


@dataclass(frozen=True, slots=True)
class Price:
    meter: str
    currency: str
    unit_rate: Decimal
    effective_from: date
    price_id: str = ""


def price_on(prices: Sequence[Price], meter: str, currency: str, on: date) -> Price | None:
    """The approved price in force on a day: the latest one that started on or before it."""
    live = [
        p for p in prices if p.meter == meter and p.currency == currency and p.effective_from <= on
    ]
    return max(live, key=lambda p: p.effective_from) if live else None


def money(amount: Decimal, currency: str, minor_units: Mapping[str, int]) -> Decimal:
    return amount.quantize(Decimal(1).scaleb(-minor_units[currency]), rounding=ROUND_HALF_EVEN)


def indicative_cost(
    prices: Sequence[Price],
    units: Mapping[str, int],
    *,
    month_start: date,
    minor_units: Mapping[str, int],
) -> list[dict[str, Any]]:
    """One entry per currency that has at least one price in force that month: each line's units, rate and amount, the total, and whether every meter with usage was priced. Strings for money, so no float ever leaves."""
    currencies = sorted({p.currency for p in prices if p.currency in minor_units})
    out: list[dict[str, Any]] = []
    for cur in currencies:
        lines: list[dict[str, Any]] = []
        total = Decimal(0)
        complete = True
        priced_any = False
        for meter, label in METERS.items():
            n = int(units.get(meter, 0))
            p = price_on(prices, meter, cur, month_start)
            if p is None:
                if n > 0:
                    complete = False
                continue
            priced_any = True
            amount = money(Decimal(n) * p.unit_rate, cur, minor_units)
            total += amount
            lines.append(
                {
                    "meter": meter,
                    "label": label,
                    "units": n,
                    "unit_rate": format(p.unit_rate.normalize(), "f"),
                    "amount": format(amount, "f"),
                    "price_from": p.effective_from.isoformat(),
                }
            )
        if priced_any:
            out.append(
                {
                    "currency": cur,
                    "lines": lines,
                    "total": format(money(total, cur, minor_units), "f"),
                    "complete": complete,
                }
            )
    return out
