"""SLOs, error budgets and multi-window burn-rate alerting (SRE-001 §4-6; OPS-02/03/05/23;
SRE-02/03/04).

Pure arithmetic plus a small in-process recorder. Production alerting runs the same windows as
Prometheus recording/alerting rules generated from the service catalogue; this module is the one
definition both sides use, so the rule files and the in-process `/ops/.../slos` view agree.

- `burn_rate`: how fast the error budget is being spent (1.0 = exactly on budget for the period);
- multi-window rules: a PAGE needs BOTH a long and a short window above the factor - sustained
  impact pages, a transient spike that has already stopped does not (OPS-05);
- zero-budget objectives (mandate overspend, cross-tenant leak, acknowledged-evidence loss) have no
  statistical budget: one bad event is EXHAUSTED and pages (SRE-001 §5 "no statistical budget");
- `budget_state` + `change_decision`: the §5.1 release policy from the remaining budget.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

MINUTE = 60
HOUR = 60 * MINUTE
DAY = 24 * HOUR
PERIOD_S = 30 * DAY  # the monthly SLO period


class AlertClass(StrEnum):
    PAGE = "PAGE"
    TICKET = "TICKET"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class BurnWindow:
    long_s: int
    short_s: int
    factor: float
    alert: AlertClass

    @property
    def name(self) -> str:
        return f"{_dur(self.long_s)}/{_dur(self.short_s)}"


# Google SRE workbook multi-window, multi-burn-rate set: 2% of a 30-day budget in 1 h, 5% in 6 h
# (PAGE); 10% in 3 d (TICKET).
WINDOWS: tuple[BurnWindow, ...] = (
    BurnWindow(1 * HOUR, 5 * MINUTE, 14.4, AlertClass.PAGE),
    BurnWindow(6 * HOUR, 30 * MINUTE, 6.0, AlertClass.PAGE),
    BurnWindow(3 * DAY, 6 * HOUR, 1.0, AlertClass.TICKET),
)
MIN_EVENTS = 10  # below this many events in the short window a ratio is noise, not a signal


def _dur(s: int) -> str:
    return (
        f"{s // DAY}d" if s % DAY == 0 else f"{s // HOUR}h" if s % HOUR == 0 else f"{s // MINUTE}m"
    )


def burn_rate(bad: float, total: float, target: float) -> float:
    if total <= 0:
        return 0.0
    budget = 1.0 - target
    if budget <= 0:
        return float("inf") if bad > 0 else 0.0
    return (bad / total) / budget


def budget_remaining(bad: float, total: float, target: float) -> float:
    """Fraction of the period's error budget left (1.0 untouched, <= 0 exhausted)."""
    if target >= 1.0:
        return 0.0 if bad > 0 else 1.0
    return 1.0 - burn_rate(bad, total, target)


class BudgetState(StrEnum):
    NORMAL = "NORMAL"  # > 50 % left: normal release policy
    HEIGHTENED_REVIEW = "HEIGHTENED_REVIEW"  # 25-50 %: heightened review for risky changes
    RELIABILITY_APPROVAL = (
        "RELIABILITY_APPROVAL"  # 10-25 %: reliability lead approves; reduce change
    )
    RELIABILITY_FREEZE = "RELIABILITY_FREEZE"  # < 10 %: freeze except fixes/security
    EXHAUSTED = "EXHAUSTED"  # change freeze + recovery plan + executive visibility


def budget_state(remaining: float) -> BudgetState:
    if remaining <= 0:
        return BudgetState.EXHAUSTED
    if remaining < 0.10:
        return BudgetState.RELIABILITY_FREEZE
    if remaining < 0.25:
        return BudgetState.RELIABILITY_APPROVAL
    if remaining <= 0.50:
        return BudgetState.HEIGHTENED_REVIEW
    return BudgetState.NORMAL


class ChangeClass(StrEnum):
    STANDARD = "STANDARD"  # features, refactors, routine changes
    RISKY = "RISKY"  # schema, auth path, protocol, crypto, topology
    FIX = "FIX"  # reliability fix for the affected service
    SECURITY = "SECURITY"  # security fix / vulnerability remediation


@dataclass(frozen=True, slots=True)
class ChangeDecision:
    allowed: bool
    requires: str | None  # an extra approval the change needs, if any
    reason: str


def change_decision(
    state: BudgetState, change: ChangeClass, *, frozen: bool = False
) -> ChangeDecision:
    """SRE-001 §5.1 (+ an explicit freeze, §23): fixes and security changes are never blocked by an
    error budget; everything else tightens as the budget drains."""
    if change in (ChangeClass.FIX, ChangeClass.SECURITY):
        needs = "incident_commander" if frozen or state is BudgetState.EXHAUSTED else None
        return ChangeDecision(True, needs, "fixes and security changes proceed")
    if frozen:
        return ChangeDecision(False, None, "CHANGE_FROZEN")
    if state is BudgetState.EXHAUSTED or state is BudgetState.RELIABILITY_FREEZE:
        return ChangeDecision(False, None, f"ERROR_BUDGET_{state.value}")
    if state is BudgetState.RELIABILITY_APPROVAL:
        return ChangeDecision(True, "reliability_lead", "error budget below 25%")
    if state is BudgetState.HEIGHTENED_REVIEW and change is ChangeClass.RISKY:
        return ChangeDecision(True, "heightened_review", "error budget at or below 50%")
    return ChangeDecision(True, None, "within error budget")


@dataclass(frozen=True, slots=True)
class WindowResult:
    window: str
    long_burn: float
    short_burn: float
    factor: float
    alert: AlertClass
    firing: bool


def evaluate(
    counts: Callable[[int], tuple[float, float]],
    target: float,
    windows: tuple[BurnWindow, ...] = WINDOWS,
    *,
    min_events: int = MIN_EVENTS,
) -> list[WindowResult]:
    """`counts(window_s) -> (bad, total)`. A window fires only when both of its rates exceed the
    factor and the short window holds enough events to mean something."""
    out = []
    for w in windows:
        lb, lt = counts(w.long_s)
        sb, st = counts(w.short_s)
        long_burn, short_burn = burn_rate(lb, lt, target), burn_rate(sb, st, target)
        firing = st >= min_events and long_burn >= w.factor and short_burn >= w.factor
        if target >= 1.0:  # zero budget: any bad event in the short window fires immediately
            firing = sb > 0
        out.append(WindowResult(w.name, long_burn, short_burn, w.factor, w.alert, firing))
    return out


def alert_class(results: list[WindowResult]) -> AlertClass:
    if any(r.firing and r.alert is AlertClass.PAGE for r in results):
        return AlertClass.PAGE
    if any(r.firing for r in results):
        return AlertClass.TICKET
    return AlertClass.NONE


class WindowedSli:
    """Good/total event counts in per-minute buckets over the longest window (3 days by default).

    Thread-safe; memory is fixed (2 x buckets ints). `clock` is injectable for tests."""

    def __init__(self, horizon_s: int = 3 * DAY, clock: Callable[[], float] = time.time) -> None:
        self.n = horizon_s // MINUTE
        self.clock = clock
        self.lock = threading.Lock()
        self.minute = [-1] * self.n  # the absolute minute each slot currently holds
        self.good = [0] * self.n
        self.total = [0] * self.n

    def record(self, good: bool, count: int = 1) -> None:
        m = int(self.clock() // MINUTE)
        i = m % self.n
        with self.lock:
            if self.minute[i] != m:
                self.minute[i], self.good[i], self.total[i] = m, 0, 0
            self.total[i] += count
            if good:
                self.good[i] += count

    def counts(self, window_s: int) -> tuple[float, float]:
        """(bad, total) over the last `window_s` seconds, current minute included."""
        now = int(self.clock() // MINUTE)
        span = min(self.n, max(1, window_s // MINUTE))
        bad = total = 0
        with self.lock:
            for m in range(now - span + 1, now + 1):
                i = m % self.n
                if self.minute[i] == m:
                    total += self.total[i]
                    bad += self.total[i] - self.good[i]
        return float(bad), float(total)
