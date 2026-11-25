"""Service availability evidence (ZID-CP-WF12 v2.0, WF-12.06). Pure domain, no I/O.

A contracted target, a measured history and "no data" are three different things. An empty history is never 100%, a target is never shown as an achievement, and a free or sandbox route never inherits an enterprise promise.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Shown(StrEnum):
    MEASURED = "MEASURED"
    METRIC_UNAVAILABLE = "METRIC_UNAVAILABLE"


@dataclass(frozen=True)
class Snapshot:
    service: str
    region: str
    window: str  # the counting window, e.g. 2026-10
    numerator: int  # good minutes (or good requests)
    denominator: (
        int  # eligible minutes (or requests), after maintenance and other stated exclusions
    )
    exclusions: str
    sre_validated: bool


@dataclass(frozen=True)
class Reading:
    state: Shown
    percent: float | None
    note: str


def read(s: Snapshot | None) -> Reading:
    """The one way a figure is produced for display. No snapshot, an unvalidated one or a zero denominator is METRIC_UNAVAILABLE, never 100%."""
    if s is None or s.denominator <= 0:
        return Reading(Shown.METRIC_UNAVAILABLE, None, "Not yet reported.")
    if not s.sre_validated:
        return Reading(
            Shown.METRIC_UNAVAILABLE, None, "Awaiting validation by the site reliability team."
        )
    if not 0 <= s.numerator <= s.denominator:
        return Reading(Shown.METRIC_UNAVAILABLE, None, "The figures do not reconcile.")
    return Reading(
        Shown.MEASURED,
        round(100 * s.numerator / s.denominator, 3),
        f"{s.window}, {s.service}, {s.region}. {s.exclusions}".strip(),
    )


def reconciles(s: Snapshot, raw_good: int, raw_total: int, *, tolerance: float = 0.001) -> bool:
    """A published figure must be reproducible from the raw telemetry within an agreed tolerance (a fraction of a percentage point)."""
    if s.denominator <= 0 or raw_total <= 0:
        return False
    return abs(100 * s.numerator / s.denominator - 100 * raw_good / raw_total) <= tolerance


@dataclass(frozen=True)
class SlaTerm:
    tier: str
    target_percent: float
    covered_tiers: tuple[str, ...]


def sla_statement(t: SlaTerm, *, customer_tier: str) -> str | None:
    """A target is shown only to a customer on a covered tier, and is worded as a target. Free and sandbox never see one."""
    if customer_tier not in t.covered_tiers or customer_tier in ("free", "sandbox"):
        return None
    return f"The service level target for the {t.tier} plan is {t.target_percent}%. This is a contractual target, not a measured result."
