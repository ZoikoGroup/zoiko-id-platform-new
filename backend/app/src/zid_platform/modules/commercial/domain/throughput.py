"""A plan's request allowance (requests per minute per company). Pure domain, no I/O.

The base is the platform setting (the DEVELOPER allowance, and what a company with no plan gets); a plan allows a multiple of it, from
`config/entitlements/plan-throughput.yaml`. A multiplier of 0 is "no limit" (contracted), and a base of 0 means the limiter is off for everyone."""

from __future__ import annotations

from collections.abc import Mapping

PLANS = ("DEVELOPER", "LAUNCH", "BUSINESS", "SCALE", "ENTERPRISE")


class ThroughputError(ValueError):
    pass


def check(multipliers: Mapping[str, object]) -> dict[str, int]:
    """The multipliers, validated: every plan present, each a whole number >= 0, and never less for a bigger plan (until the contracted one)."""
    out: dict[str, int] = {}
    for plan in PLANS:
        m = multipliers.get(plan)
        if not isinstance(m, int) or isinstance(m, bool) or m < 0:
            raise ThroughputError(f"{plan} needs a whole-number multiplier of 0 or more")
        out[plan] = m
    ladder = [out[p] for p in PLANS[:-1]]
    if ladder != sorted(ladder) or ladder[0] < 1:
        raise ThroughputError("a bigger plan must never allow fewer requests than a smaller one, and DEVELOPER is the base (1 or more)")
    return out


def request_limit(plan_code: str, base: int, multipliers: Mapping[str, int]) -> int:
    """Requests per minute for this plan; 0 = unlimited. An unknown plan code gets the base allowance."""
    if base <= 0:
        return 0
    m = multipliers.get(plan_code, 1)
    return 0 if m == 0 else base * m
