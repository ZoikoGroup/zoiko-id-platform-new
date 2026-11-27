"""The Verify People price table and the estimate built from it (the pricing page, version 5.0). Pure rules, no I/O.

The table is PROPOSED: the numbers buyers currently see, not an approved price and not an invoice rate. An estimate is a count times a rate with a spend floor, rounded once to the currency's minor units, and it
says which table version it used. A plan with no published price (Enterprise) is refused, never guessed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from zid_platform.modules.verify_api.domain.model import PartnerError

_CENT = Decimal("0.01")
MAX_VOLUME = 1_000_000_000


@dataclass(frozen=True)
class Plan:
    code: str
    name: str
    charge: Decimal | None
    minimum_monthly: Decimal | None
    custom: bool


@dataclass(frozen=True)
class Table:
    version: str
    status: str
    currency: str
    plans: Mapping[str, Plan]


def parse(raw: Mapping[str, Any]) -> Table:
    plans: dict[str, Plan] = {}
    for code, p in raw["plans"].items():
        if p.get("custom"):
            plans[code] = Plan(code, str(p["name"]), None, None, True)
            continue
        charge, floor = Decimal(str(p["charge"])), Decimal(str(p["minimum_monthly"]))
        if charge <= 0 or floor < 0:
            raise ValueError(
                f"plan {code}: the charge must be above zero and the minimum not negative"
            )
        plans[code] = Plan(code, str(p["name"]), charge, floor, False)
    if raw.get("minimum_model") != "FLOOR":
        raise ValueError("only a spend floor is modelled")
    return Table(str(raw["version"]), str(raw["status"]), str(raw["currency"]), plans)


def estimate(table: Table, plan: str, verifications: int) -> dict[str, Any]:
    p = table.plans.get(plan)
    if p is None:
        raise PartnerError("PLAN_UNKNOWN", f"There is no plan called {plan}.")
    if p.custom or p.charge is None or p.minimum_monthly is None:
        raise PartnerError(
            "PRICE_ON_REQUEST", f"{p.name} is priced by agreement. No estimate is offered."
        )
    if not 0 <= verifications <= MAX_VOLUME:
        raise PartnerError(
            "VOLUME_INVALID", "The number of verifications must be between 0 and 1,000,000,000."
        )
    usage = (p.charge * verifications).quantize(_CENT, ROUND_HALF_EVEN)
    total = max(usage, p.minimum_monthly)
    return {
        "plan": p.name, "currency": table.currency, "verifications": verifications, "rate_per_verification": format(p.charge, "f"), "usage": format(usage, "f"),
        "monthly_minimum": format(p.minimum_monthly, "f"), "total": format(total, "f"), "minimum_applied": usage < p.minimum_monthly,
        "table_version": table.version, "table_status": table.status,
        "note": "An indicative estimate from proposed prices. It excludes tax and optional services and is not an invoice.",
    }  # fmt: skip
