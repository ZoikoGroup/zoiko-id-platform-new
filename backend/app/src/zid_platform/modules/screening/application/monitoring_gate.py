"""The monitoring scope check (ZID-RCG-003 RCG-G12; CR-081): asked before any ongoing monitoring runs.

    require_monitoring   raises MonitoringNotApproved (enforce) or logs (shadow) unless an approval covers this country, kind and sector now
    monitoring_allowed   the decision itself, for a caller that wants to show it

Every ongoing monitoring feature asks this first: today the scheduled sanctions re-screen (`passes.rescreen_pass`, kind SANCTIONS_ONGOING). PEP,
adverse media, transaction, bank ownership and crypto monitoring do not exist yet; when one is built it calls `require_monitoring` with its kind.
Switch `ZID_MONITORING_SCOPE` (configured at start-up): shadow (default) logs, enforce holds, off skips (refused outside local, test and dev).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime

from zid_platform.modules.screening.domain import monitoring_scope as ms
from zid_platform.modules.screening.infrastructure.monitoring_approvals import monitoring_approvals

_LOG = logging.getLogger("zid.regulatory")
_MODE = "shadow"


class MonitoringNotApproved(ValueError):
    def __init__(self, decision: ms.MonitoringDecision) -> None:
        super().__init__(",".join(decision.reasons))
        self.decision = decision


def configure(mode: str) -> None:
    global _MODE
    if mode not in ("off", "shadow", "enforce"):
        raise ValueError("ZID_MONITORING_SCOPE is off, shadow or enforce")
    _MODE = mode


def mode() -> str:
    return _MODE


def monitoring_allowed(
    *,
    country: str,
    kind: ms.Kind,
    sector: str = ms.ANY_SECTOR,
    at: datetime,
    approvals: Iterable[ms.MonitoringApproval] | None = None,
) -> ms.MonitoringDecision:
    return ms.decide(
        monitoring_approvals() if approvals is None else approvals,
        country=country,
        kind=kind,
        sector=sector,
        at=at,
    )


def require_monitoring(
    *,
    country: str,
    kind: ms.Kind,
    sector: str = ms.ANY_SECTOR,
    at: datetime,
    approvals: Iterable[ms.MonitoringApproval] | None = None,
) -> None:
    if _MODE == "off":
        return
    d = monitoring_allowed(country=country, kind=kind, sector=sector, at=at, approvals=approvals)
    if d.allowed:
        return
    _LOG.warning(
        "monitoring scope %s: %s in %s for %s (%s)",
        "held" if _MODE == "enforce" else "would hold (shadow)",
        kind.value,
        country or "-",
        sector,
        ",".join(d.reasons),
    )
    if _MODE == "enforce":
        raise MonitoringNotApproved(d)
