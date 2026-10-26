"""Rating: usage and entitlement in, rated lines out (ZID-COM-001 §6, §7, §13, §30; CR-026). Pure domain.

Everything here is `Decimal`. A float anywhere is a defect: `rate()` refuses one outright. Each line is rounded
exactly once, `ROUND_HALF_EVEN` to the currency's minor unit, and the subtotal is the sum of the rounded
lines, so the bill always adds up line by line.

Rules, all from the proposal:

- overage = max(0, observed - included) x unit rate, per unit of the price book line's divisor (§13);
- a plan change mid-month splits the period at the effective instant: the platform fee and the included
  allowances are prorated by the share of the period each plan was active, and usage is rated against each
  segment's own allowance, so an upgrade can never double the included volume;
- extra connections are counted at the cut-off, not prorated;
- a credit is a negative line that can never take the invoice below zero (the excess is a credit balance);
- a meter with no price in the book for that plan is not charged and is reported as a warning, never guessed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from itertools import pairwise

MINOR_UNITS = {"USD": 2, "EUR": 2, "GBP": 2, "JPY": 0}
SYMBOLS = {"USD": "$", "EUR": "EUR ", "GBP": "GBP ", "JPY": "JPY "}

METER_SKU = {
    "MAH": "ZID-HUM",
    "AUTH_EVENT": "ZID-AUTH",
    "POLICY_DECISION": "ZID-PDP",
    "M2M_GRANT": "ZID-M2M",
    "VC_ISSUED": "ZID-VCI",
    "VC_PRESENTED": "ZID-VCP",
}
# the plan column that holds each meter's included allowance; VC meters have none (metered from the first)
INCLUDED_FIELD = {
    "MAH": "mah_included",
    "AUTH_EVENT": "auth_events_included",
    "POLICY_DECISION": "policy_decisions_included",
    "M2M_GRANT": "m2m_grants_included",
}
CONNECTION_SKU = {"saml_sso": "ZID-FED", "scim": "ZID-SCIM"}
BASE_SKU = "ZID-BASE"


class RatingError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code, self.detail = code, detail


@dataclass(frozen=True, slots=True)
class PriceLine:
    unit_rate: Decimal
    unit_divisor: int = 1
    minimum_amount: Decimal | None = None


@dataclass(frozen=True, slots=True)
class PlanTerms:
    plan_code: str
    platform_fee: Decimal
    fee_is_floor: bool
    included: Mapping[str, Decimal | None]  # meter -> allowance; None = negotiated or none
    sso_included: int | None


@dataclass(frozen=True, slots=True)
class Segment:
    """One stretch of the period on one plan, with that plan's prices from the entitlement's price book."""

    plan: PlanTerms
    start: datetime
    end: datetime
    lines: Mapping[str, PriceLine]  # sku -> price


@dataclass(frozen=True, slots=True)
class Credit:
    amount: Decimal
    reason_code: str
    reverses: str | None = None  # the rated line it reverses, when it reverses one


@dataclass(frozen=True, slots=True)
class RatedLine:
    kind: str  # FEE | OVERAGE | CONNECTION | MINIMUM | CREDIT
    sku: str | None
    meter: str | None
    plan_code: str | None
    segment: int
    included: Decimal | None
    chargeable: Decimal | None
    unit_rate: Decimal | None
    unit_divisor: int | None
    units: Decimal | None
    formula: str
    net: Decimal
    reverses: str | None = None


@dataclass(frozen=True, slots=True)
class RatedPeriod:
    currency: str
    lines: tuple[RatedLine, ...]
    subtotal: Decimal
    credit_balance: Decimal
    warnings: tuple[str, ...] = field(default_factory=tuple)


def quantize(amount: Decimal, currency: str) -> Decimal:
    places = MINOR_UNITS.get(currency)
    if places is None:
        raise RatingError("CURRENCY_UNSUPPORTED", currency)
    return amount.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)


def _no_floats(*values: object) -> None:
    for v in values:
        if isinstance(v, float):
            raise RatingError("FLOAT_REFUSED", "money and quantities are Decimal, never float")


def _num(v: Decimal) -> str:
    """12,345 or 0.015: thousands separators, no trailing zeros, no exponent."""
    t = format(v.normalize(), "f")
    whole, _, frac = t.partition(".")
    sign = "-" if whole.startswith("-") else ""
    whole = f"{int(whole.lstrip('-')):,}"
    return f"{sign}{whole}.{frac}" if frac else f"{sign}{whole}"


def _money(v: Decimal, currency: str) -> str:
    return f"{SYMBOLS.get(currency, currency + ' ')}{_num(v)}"


def _formula(chargeable: Decimal, divisor: int, rate: Decimal, currency: str) -> str:
    if divisor == 1:
        return f"{_num(chargeable)} x {_money(rate, currency)}"
    return f"{_num(chargeable)} / {_num(Decimal(divisor))} x {_money(rate, currency)}"


def rate(
    *,
    currency: str,
    period_start: datetime,
    period_end: datetime,
    segments: Sequence[Segment],
    usage: Sequence[Mapping[str, Decimal]],
    connections: Mapping[str, int],
    connection_extras: Mapping[str, int] | None = None,
    credits: Sequence[Credit] = (),
) -> RatedPeriod:
    """Rate one tenant's period. `usage[i]` is the observed quantity per meter inside `segments[i]`."""
    if len(segments) != len(usage) or not segments:
        raise RatingError("SEGMENTS_INVALID", "one usage map per segment")
    for s in segments:
        _no_floats(s.plan.platform_fee, *(ln.unit_rate for ln in s.lines.values()))
    for u in usage:
        _no_floats(*u.values())
    for c in credits:
        _no_floats(c.amount)
        if c.amount <= 0:
            raise RatingError("CREDIT_INVALID", "a credit is a positive amount")
    period = (period_end - period_start).total_seconds()
    if period <= 0:
        raise RatingError("PERIOD_INVALID")
    ordered = sorted(segments, key=lambda s: s.start)
    if ordered[0].start != period_start or ordered[-1].end != period_end:
        raise RatingError("SEGMENTS_INVALID", "segments must cover the period")
    for a, b in pairwise(ordered):
        if a.end != b.start:
            raise RatingError("SEGMENTS_INVALID", "segments must be contiguous")

    lines: list[RatedLine] = []
    warnings: list[str] = []
    for i, seg in enumerate(segments):
        fraction = Decimal((seg.end - seg.start).total_seconds()) / Decimal(period)
        plan = seg.plan
        base = seg.lines.get(BASE_SKU)
        fee = base.unit_rate if base else plan.platform_fee
        fee_net = quantize(fee * fraction, currency)
        share = (
            ""
            if fraction == 1
            else f" x {_num(fraction.quantize(Decimal('0.000001')))} of the month"
        )
        lines.append(
            RatedLine(
                "FEE",
                BASE_SKU,
                None,
                plan.plan_code,
                i,
                None,
                None,
                fee,
                1,
                fraction,
                f"{_money(fee, currency)} {plan.plan_code.title()} platform fee{share}"
                + (" (floor)" if plan.fee_is_floor else ""),
                fee_net,
            )
        )
        for meter, sku in METER_SKU.items():
            observed = usage[i].get(meter, Decimal(0))
            if observed <= 0:
                continue
            allowance = plan.included.get(meter) if meter in INCLUDED_FIELD else Decimal(0)
            if meter in INCLUDED_FIELD and allowance is None:
                warnings.append(
                    f"{meter}: negotiated allowance on {plan.plan_code}; not rated here"
                )
                continue
            included = (allowance or Decimal(0)) * fraction
            chargeable = max(Decimal(0), observed - included)
            if chargeable == 0:
                continue
            price = seg.lines.get(sku)
            if price is None:
                warnings.append(
                    f"{meter}: {_num(chargeable)} above the allowance on {plan.plan_code} has no price in the book"
                )
                continue
            units = chargeable / Decimal(price.unit_divisor)
            lines.append(
                RatedLine(
                    "OVERAGE",
                    sku,
                    meter,
                    plan.plan_code,
                    i,
                    included,
                    chargeable,
                    price.unit_rate,
                    price.unit_divisor,
                    units,
                    _formula(chargeable, price.unit_divisor, price.unit_rate, currency),
                    quantize(units * price.unit_rate, currency),
                )
            )

    last = len(segments) - 1
    final = ordered[-1] if ordered else segments[-1]
    extras = connection_extras or {}
    for capability, sku in CONNECTION_SKU.items():
        active = connections.get(capability, 0)
        included_conn = (final.plan.sso_included or 0) if capability == "saml_sso" else 0
        if capability == "saml_sso" and final.plan.sso_included is None:
            continue  # negotiated
        extra = max(0, active - included_conn - extras.get(capability, 0))
        if extra == 0:
            continue
        price = final.lines.get(sku)
        if price is None:
            warnings.append(f"{capability}: {extra} extra connection(s) have no price in the book")
            continue
        lines.append(
            RatedLine(
                "CONNECTION",
                sku,
                capability,
                final.plan.plan_code,
                last,
                Decimal(included_conn + extras.get(capability, 0)),
                Decimal(extra),
                price.unit_rate,
                1,
                Decimal(extra),
                f"{extra} extra x {_money(price.unit_rate, currency)}",
                quantize(Decimal(extra) * price.unit_rate, currency),
            )
        )

    subtotal = sum((ln.net for ln in lines), Decimal(0))
    base_line = final.lines.get(BASE_SKU)
    minimum = base_line.minimum_amount if base_line else None
    if minimum is not None and subtotal < minimum:
        top_up = quantize(minimum - subtotal, currency)
        lines.append(
            RatedLine(
                "MINIMUM",
                BASE_SKU,
                None,
                final.plan.plan_code,
                last,
                None,
                None,
                None,
                None,
                None,
                f"True-up to the {_money(minimum, currency)} minimum",
                top_up,
            )
        )
        subtotal += top_up

    remaining = subtotal
    for c in credits:
        applied = quantize(min(c.amount, remaining), currency)
        if applied > 0:
            lines.append(
                RatedLine(
                    "CREDIT",
                    None,
                    None,
                    None,
                    last,
                    None,
                    None,
                    None,
                    None,
                    None,
                    f"Credit {c.reason_code}: {_money(applied, currency)}",
                    -applied,
                    c.reverses,
                )
            )
            remaining -= applied
    credit_balance = quantize(
        sum((c.amount for c in credits), Decimal(0)) - (subtotal - remaining), currency
    )
    return RatedPeriod(currency, tuple(lines), remaining, credit_balance, tuple(warnings))


def sla_credit(
    base_fee: Decimal,
    shortfall: Decimal,
    steps: Sequence[tuple[Decimal, Decimal]],
    cap: Decimal,
    currency: str,
) -> Decimal:
    """A service credit on the affected base fee. `steps` are (minimum shortfall, credit share) pairs and are
    DATA supplied by the signed SLA schedule: the proposal names 5/10/20% candidates but no thresholds, so none
    is assumed here. The share never exceeds `cap`. Returns 0 when no step is reached."""
    _no_floats(base_fee, shortfall, cap, *(x for s in steps for x in s))
    share = Decimal(0)
    for threshold, pct in sorted(steps):
        if shortfall >= threshold:
            share = pct
    share = min(share, cap)
    return quantize(base_fee * share, currency)
