"""The fail-closed usage and budget control hook (ZID-AG-WF-001 sections 08 and 12; CR-091).

Section 08: "Q1 requires a fail-closed control hook; it does not establish final authorization thresholds." Section 12: an agent that
overspends its budget or exceeds its usage is blocked by the Q2 policy hook before the request or step, with BUDGET_POLICY_DENY and no
undocumented fallback.

So the hook is asked before a run starts and before each step (each read, the rule evaluation, each proposal write and a model request).
The hook that ships has NO thresholds: it permits. The limits themselves (quotas, spend, schedules) are Q2 decisions (D-07) and are not
invented here. What is enforced now is the failure mode: an error, a timeout or any answer other than exactly ALLOW is a denial.

A deployment (or a test) hands its own hook in as `app.state.access_readiness_usage_policy`. The hook receives ids and the step name only,
no connection: it holds whatever resources it needs, and a hook that hangs is cut off by the timeout without leaving a query half done.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

BUDGET_POLICY_DENY = "BUDGET_POLICY_DENY"
ALLOW = "ALLOW"
DENY = "DENY"
DEFAULT_TIMEOUT_SECONDS = 2.0

# the steps the hook is asked about (bounded: they also name the evidence)
STEP_RUN_START = "run.start"
STEP_EVALUATE = "evaluate_rules"
STEP_EXPLAIN = "explain_model_request"


@dataclass(frozen=True, slots=True)
class UsageRequest:
    tenant_id: str
    agent_id: str
    run_id: str
    step: str
    purpose: str


# answers "ALLOW" or "DENY"; anything else, an exception or no answer in time is a denial
UsagePolicy = Callable[[UsageRequest], Awaitable[object]]


async def no_thresholds(_request: UsageRequest) -> str:
    """The Q1 hook: no limit is set, so every step is permitted. The Q2 limits replace this, not the checks around it."""
    return ALLOW


async def usage_denial(
    policy: UsagePolicy | None, request: UsageRequest, *, timeout_seconds: float
) -> str | None:
    """None when the step may go ahead; otherwise why not (DENY, TIMEOUT, ERROR or UNKNOWN). Never raises."""
    hook = policy if policy is not None else no_thresholds
    try:
        answer = await asyncio.wait_for(hook(request), timeout=timeout_seconds)
    except TimeoutError:
        return "TIMEOUT"
    except Exception:  # noqa: BLE001 - a hook that cannot answer never permits anything
        return "ERROR"
    if isinstance(answer, str) and answer == ALLOW:
        return None
    if isinstance(answer, str) and answer == DENY:
        return DENY
    return "UNKNOWN"
