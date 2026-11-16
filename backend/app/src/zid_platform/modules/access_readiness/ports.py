"""Public interface of the access_readiness module (CR-091). Other modules import ONLY this.

The module reads other modules only through their ports and exposes nothing that changes a right: what it offers is the worker's pass over
queued runs (so a deployment can drain them) and the cancellation of an agent's queued runs (the kill switch for one agent).
"""

from __future__ import annotations

from zid_platform.modules.access_readiness.application.common import Runtime
from zid_platform.modules.access_readiness.application.runs import cancel_queued, execute_queued
from zid_platform.modules.access_readiness.domain.policy import ReadinessError
from zid_platform.modules.access_readiness.domain.rules import (
    POLICY_VERSION,
    RULE_SET_VERSION,
    evaluate_run,
)

__all__ = [
    "POLICY_VERSION",
    "RULE_SET_VERSION",
    "ReadinessError",
    "Runtime",
    "cancel_queued",
    "evaluate_run",
    "execute_queued",
]
