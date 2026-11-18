"""A company's monthly verification spend budget, as pure rules (ZID-DTV-WF-01 WF01-014; CR-107).

1. No budget row means no budget. There is no default amount: the owners set one, or none.
2. Spend is counted in the budget's own currency only. A cost recorded in another currency is not converted and is reported as `other_currencies`, never
   silently added or dropped.
3. At or over the limit, no new verification starts that month: `VERIFICATION_BUDGET_EXCEEDED`. A session already started always finishes.
4. A limit of zero is allowed and means "none this month".
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from zid_platform.modules.verification.domain.verification import VerificationError

MAX_LIMIT = Decimal(1000000000000)  # a sanity ceiling, not a recommendation
_CURRENCY = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True, slots=True)
class Budget:
    monthly_limit: Decimal
    currency: str


def parse_budget(limit: object, currency: object) -> Budget:
    """A decimal string (or int) at or above zero with at most six places, and a three-letter currency. Floats are refused: money is never a float."""
    if isinstance(limit, bool | float) or not isinstance(limit, str | int):
        raise VerificationError("BUDGET_INVALID", "monthly_limit")
    try:
        value = Decimal(str(limit))
    except InvalidOperation as e:
        raise VerificationError("BUDGET_INVALID", "monthly_limit") from e
    if not value.is_finite() or value < 0 or value > MAX_LIMIT or -value.as_tuple().exponent > 6:  # type: ignore[operator]
        raise VerificationError("BUDGET_INVALID", "monthly_limit")
    if not isinstance(currency, str) or not _CURRENCY.match(currency):
        raise VerificationError("BUDGET_INVALID", "currency")
    return Budget(value, currency)


def month_start(now: datetime) -> datetime:
    """The first instant of the calendar month in UTC."""
    n = now.astimezone(UTC)
    return n.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def spent_in(budget: Budget, spent_by_currency: Mapping[str, Decimal]) -> Decimal:
    return spent_by_currency.get(budget.currency, Decimal(0))


def other_currencies(budget: Budget, spent_by_currency: Mapping[str, Decimal]) -> list[str]:
    return sorted(c for c, v in spent_by_currency.items() if c != budget.currency and v > 0)


def require_within_budget(budget: Budget | None, spent_by_currency: Mapping[str, Decimal]) -> None:
    if budget is None:
        return
    if spent_in(budget, spent_by_currency) >= budget.monthly_limit:
        raise VerificationError(
            "VERIFICATION_BUDGET_EXCEEDED", "monthly verification budget reached"
        )
