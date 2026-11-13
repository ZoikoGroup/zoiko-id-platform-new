"""FX for reporting (ZID-COM-WF-2026-11 section 05.2). Pure.

A rate set says how many US dollars one unit of a currency is worth, from a named source on a named date. There is no default: a currency the set does not list is
not converted, it is reported as not converted. Conversion rounds half-to-even once, on the total, never per line and never up.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_EVEN, Decimal

_CENT = Decimal("0.01")


def to_usd(
    amounts: Mapping[str, Decimal], usd_per_unit: Mapping[str, Decimal] | None
) -> dict[str, object]:
    """Add up amounts in several currencies as US dollars. `complete` is false when any currency could not be converted; those are named, never dropped silently."""
    rates = usd_per_unit or {}
    total = Decimal(0)
    used: list[dict[str, str]] = []
    missing: list[str] = []
    for currency, value in sorted(amounts.items()):
        if currency == "USD":
            total += value
            continue
        rate = rates.get(currency)
        if rate is None:
            missing.append(currency)
            continue
        total += value * rate
        used.append({"currency": currency, "usd_per_unit": str(rate)})
    return {
        "usd": str(total.quantize(_CENT, rounding=ROUND_HALF_EVEN)),
        "complete": not missing,
        "rates_used": used,
        "not_converted": missing,
    }
