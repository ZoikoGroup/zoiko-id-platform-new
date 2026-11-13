"""Family rates and the public estimate (ZID-UX-PRICE-004 WF-P04, sections 06.1, 06.2). Pure.

A rate line says, for one service family, one payer country and one billing period: the currency, the exact unit, the units included, the amount per unit above that, and an optional minimum that
declares itself either a spend FLOOR (the charge is the greater of the minimum and the usage) or a PLATFORM_FEE (the minimum is billed in addition to the usage). The two are never combined: a rate has
at most one minimum and says which it is. Amounts are exact decimals rounded once, to the currency's minor units, at the end of each line; a currency with no declared minor-unit rule is refused, not guessed.

Nothing here invents a rate. An estimate is built only from lines an ACTIVE rate card holds, for a service the market matrix has released, and it is never binding: it names the card version, the hash of exactly
what it priced, and when it stops being offered.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from zid_platform.modules.commercial.domain.rating import MINOR_UNITS, quantize

PERIODS = ("MONTHLY", "ANNUAL")
MODELS = ("FLOOR", "PLATFORM_FEE")
ESTIMATE_TTL_S = 3600
_MAX_VOLUME = 1_000_000_000


class RateError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Rate:
    family: str
    country: str
    period: str
    currency: str
    charge_unit: str
    included_units: int
    unit_amount: Decimal
    minimum_amount: Decimal | None
    minimum_model: str | None


@dataclass(frozen=True, slots=True)
class Line:
    family: str
    period: str
    currency: str
    volume: int
    included_units: int
    billable_units: int
    usage: Decimal
    minimum_amount: Decimal | None
    minimum_model: str | None
    amount: Decimal
    formula: str


def rate_problems(r: Rate) -> list[str]:
    """Why a rate line cannot be recorded. Every problem is named."""
    out: list[str] = []
    if r.period not in PERIODS:
        out.append("PERIOD_INVALID")
    if r.currency not in MINOR_UNITS:
        out.append("CURRENCY_UNSUPPORTED")
    if r.included_units < 0:
        out.append("INCLUDED_NEGATIVE")
    if r.unit_amount < 0:
        out.append("UNIT_AMOUNT_NEGATIVE")
    if (r.minimum_amount is None) != (r.minimum_model is None):
        out.append(
            "MINIMUM_MODEL_REQUIRED" if r.minimum_amount is not None else "MINIMUM_AMOUNT_REQUIRED"
        )
    if r.minimum_amount is not None and r.minimum_amount <= 0:
        out.append("MINIMUM_NOT_POSITIVE")
    if r.minimum_model is not None and r.minimum_model not in MODELS:
        out.append("MINIMUM_MODEL_INVALID")
    if r.unit_amount <= 0 and r.minimum_amount is None:
        out.append("NO_PRICE")
    if not r.charge_unit.strip() or len(r.charge_unit.strip()) < 3:
        out.append("CHARGE_UNIT_MISSING")
    return out


def _fmt(v: Decimal, currency: str) -> str:
    return f"{currency} {quantize(v, currency)}"


def charge(rate: Rate, volume: int) -> Line:
    """One line of an estimate. `volume` is the number of units in the period. Floor or fee is applied exactly as the rate declares it."""
    if isinstance(volume, bool) or not isinstance(volume, int) or not 0 <= volume <= _MAX_VOLUME:
        raise RateError("VOLUME_INVALID", str(volume))
    problems = rate_problems(rate)
    if problems:
        raise RateError(problems[0], ",".join(problems))
    billable = max(0, volume - rate.included_units)
    usage = Decimal(billable) * rate.unit_amount
    if rate.minimum_model == "FLOOR":
        assert rate.minimum_amount is not None
        total = max(rate.minimum_amount, usage)
        how = f"the greater of the {_fmt(rate.minimum_amount, rate.currency)} minimum spend and usage of {_fmt(usage, rate.currency)}"
    elif rate.minimum_model == "PLATFORM_FEE":
        assert rate.minimum_amount is not None
        total = rate.minimum_amount + usage
        how = f"{_fmt(rate.minimum_amount, rate.currency)} platform fee plus usage of {_fmt(usage, rate.currency)}"
    else:
        total = usage
        how = f"usage of {_fmt(usage, rate.currency)}"
    unit_text = f"{billable:,} units above the {rate.included_units:,} included x {_fmt(rate.unit_amount, rate.currency)}"
    return Line(
        family=rate.family,
        period=rate.period,
        currency=rate.currency,
        volume=volume,
        included_units=rate.included_units,
        billable_units=billable,
        usage=quantize(usage, rate.currency),
        minimum_amount=quantize(rate.minimum_amount, rate.currency)
        if rate.minimum_amount is not None
        else None,
        minimum_model=rate.minimum_model,
        amount=quantize(total, rate.currency),
        formula=f"{unit_text}; {how}",
    )


def totals(lines: Sequence[Line]) -> dict[str, Decimal]:
    """Totals per currency. Amounts in different currencies are never added together."""
    out: dict[str, Decimal] = {}
    for ln in lines:
        out[ln.currency] = out.get(ln.currency, Decimal(0)) + ln.amount
    return {c: quantize(v, c) for c, v in sorted(out.items())}


def annual_comparison(monthly: Rate, annual: Rate, monthly_volume: int) -> dict[str, str] | None:
    """What twelve months on the monthly rate cost against one year on the annual rate, for the same expected volume, in the same currency. None unless both exist, share a currency, and the
    annual one is actually cheaper: a saving is claimed only when the two signed rates compute it."""
    if monthly.currency != annual.currency:
        return None
    twelve = charge(monthly, monthly_volume).amount * 12
    yearly = charge(annual, monthly_volume * 12).amount
    if yearly >= twelve:
        return None
    return {
        "currency": monthly.currency,
        "twelve_months": str(quantize(twelve, monthly.currency)),
        "annual": str(quantize(yearly, monthly.currency)),
        "saving": str(quantize(twelve - yearly, monthly.currency)),
    }


def quote_hash(scope: Mapping[str, object], card_version: int, lines: Sequence[Line]) -> str:
    """SHA-256 of canonical JSON of exactly what was priced: the scope, the card version and every line. The same inputs always give the same hash; any change gives another."""
    body = {
        "scope": scope,
        "card_version": card_version,
        "lines": [
            {
                "family": ln.family,
                "period": ln.period,
                "currency": ln.currency,
                "volume": ln.volume,
                "amount": str(ln.amount),
                "formula": ln.formula,
            }
            for ln in sorted(lines, key=lambda x: (x.family, x.period))
        ],
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
