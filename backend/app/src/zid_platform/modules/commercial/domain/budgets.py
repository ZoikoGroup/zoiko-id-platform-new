"""Anti-bill-shock: budgets, included-quota warnings and the month-end projection (ZID-COM-001 §7; CR-029).

A budget only ever notifies. Nothing here can throttle or block anything, least of all a security path.

- Thresholds default to 50, 80 and 100 percent (§7) and each fires once per budget per month.
- Included-quota warnings fire at the same thresholds for every metered allowance, budget or not.
- The projection scales month-to-date usage by the elapsed share of the month and rates it with the same
  rating function as the invoice, so the preview and the bill cannot disagree on arithmetic. It is an estimate
  with its assumptions, never a quotation, and there is none in the first six hours of a month.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, Decimal

DEFAULT_THRESHOLDS = (50, 80, 100)
NO_PROJECTION_BEFORE = timedelta(hours=6)
LOW_CONFIDENCE_BEFORE = timedelta(days=3)


class BudgetError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code, self.detail = code, detail


@dataclass(frozen=True, slots=True)
class Budget:
    budget_id: str
    meter: str | None  # None: the whole bill, in money
    limit: Decimal
    thresholds: tuple[int, ...] = DEFAULT_THRESHOLDS


def check_thresholds(thresholds: Sequence[int]) -> tuple[int, ...]:
    out = tuple(sorted(set(thresholds)))
    if not out or len(out) > 5 or any(not 1 <= t <= 200 for t in out):
        raise BudgetError("THRESHOLDS_INVALID", "one to five whole percentages between 1 and 200")
    return out


def elapsed_fraction(start: datetime, end: datetime, now: datetime) -> Decimal:
    total = (end - start).total_seconds()
    done = min(max((now - start).total_seconds(), 0.0), total)
    return Decimal(str(done)) / Decimal(str(total))


def projection_status(start: datetime, now: datetime) -> str:
    """NONE in the first six hours, LOW in the first three days, NORMAL after."""
    age = now - start
    if age < NO_PROJECTION_BEFORE:
        return "NONE"
    if age < LOW_CONFIDENCE_BEFORE:
        return "LOW"
    return "NORMAL"


def project_usage(usage: Mapping[str, Decimal], fraction: Decimal) -> dict[str, Decimal]:
    """Month-to-date quantities scaled to the whole month, rounded up to whole units."""
    if fraction <= 0:
        return {m: q for m, q in usage.items()}
    # quantize first so a share like 1/3 (0.3333...) does not turn 30.0000000001 into 31
    return {
        m: (q / fraction).quantize(Decimal("0.000001")).to_integral_value(rounding=ROUND_CEILING)
        for m, q in usage.items()
    }


def crossed(spent: Decimal, limit: Decimal, thresholds: Sequence[int]) -> list[int]:
    """Every threshold `spent` has reached against `limit` (a zero limit is reached at once)."""
    if limit <= 0:
        return list(thresholds) if spent > 0 else []
    pct = spent * 100 / limit
    return [t for t in thresholds if pct >= t]


def quota_crossings(
    usage: Mapping[str, Decimal],
    allowances: Mapping[str, Decimal | None],
    thresholds: Sequence[int] = DEFAULT_THRESHOLDS,
) -> dict[str, list[int]]:
    """Per meter with a finite allowance: which included-quota thresholds month-to-date usage has reached."""
    out: dict[str, list[int]] = {}
    for meter, allowance in allowances.items():
        if allowance is None or allowance <= 0:
            continue
        hit = crossed(usage.get(meter, Decimal(0)), allowance, thresholds)
        if hit:
            out[meter] = hit
    return out
