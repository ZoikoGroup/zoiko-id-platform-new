"""Observability of the access readiness agent (ZID-AG-WF-001 section 11: correlated traces, redacted logs, deny counts, drift alerts,
evidence integrity; CR-091).

- Metrics through the platform's governed registry (`zid_telemetry.metrics`, scraped by the cell's collector): runs by terminal state and
  denials by reason code. The labels are a state and a code, both bounded sets; never a tenant, agent, run or person.
- Log lines name the run and its trace (the correlation id the run was requested under) and nothing else: no token, document, metadata
  string or personal field. The message is also scrubbed by the JSON formatter.
- Two mandatory alerts through `notification.ports`, the way the run notification is raised: a run stopped by policy drift (the approved
  scope digest or the policy changed since approval) and an evidence chain that does not verify. A repeat folds into the live alert (the
  alert store's correlation key), so a drift that blocks every run of an agent is one alert with a count, not one per run.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_telemetry.metrics import REGISTRY

from zid_platform.modules.access_readiness.infrastructure import store
from zid_platform.modules.notification.ports import AlertDraft, Severity, raise_alert

log = logging.getLogger("zid.access_readiness")

RUNS = REGISTRY.counter(
    "zid_access_readiness_runs_total",
    "Access readiness agent runs by terminal state",
    ("state",),
)
DENIALS = REGISTRY.counter(
    "zid_access_readiness_denials_total",
    "Access readiness policy decisions that denied, by reason code",
    ("reason",),
)
ALERTS = REGISTRY.counter(
    "zid_access_readiness_alerts_total",
    "Access readiness mandatory alerts raised, by kind and whether the alert could be raised",
    ("kind", "result"),
)

# alert kinds (notification inbox; not bus events)
POLICY_DRIFT_ALERT = "access_readiness.policy_drift"
EVIDENCE_INTEGRITY_ALERT = "access_readiness.evidence_integrity"

# the preflight codes that mean "what was approved is no longer what would run"
DRIFT_CODES = frozenset({"APPROVAL_DIGEST_MISMATCH", "POLICY_CHANGED"})


def run_log(run: dict[str, Any], message: str, *args: object, level: int = logging.INFO) -> None:
    """One log line for a run: its id and its trace, then a fixed message with codes or counts only."""
    log.log(
        level,
        "access readiness run=%s trace=%s " + message,
        run["run_id"],
        run.get("trace_id") or "-",
        *args,
    )


def count_terminal(state: str) -> None:
    RUNS.inc(state=state)


def count_denial(reason: str) -> None:
    DENIALS.inc(reason=reason)


async def _raise(conn: AsyncConnection, tenant_id: str, draft: AlertDraft) -> bool:
    """A mandatory alert that cannot be raised never changes the run or the read; the failure is logged and counted."""
    try:
        async with conn.begin_nested():
            await raise_alert(conn, tenant_id, draft, now=datetime.now(UTC))
    except Exception:  # noqa: BLE001 - the caller's outcome stands
        ALERTS.inc(kind=draft.kind, result="failed")
        return False
    ALERTS.inc(kind=draft.kind, result="raised")
    return True


async def alert_policy_drift(
    conn: AsyncConnection, run: dict[str, Any], codes: Iterable[str]
) -> bool | None:
    """Raise the drift alert when a run was stopped because its approval no longer matches. None when no drift code is present."""
    drift = sorted(set(codes) & DRIFT_CODES)
    if not drift:
        return None
    agent_id = run["agent_principal_id"]
    draft = AlertDraft(
        severity=Severity.HIGH,
        kind=POLICY_DRIFT_ALERT,
        title="An access readiness run was stopped: its approval no longer matches",
        body=(
            f"Run {run['run_id']} was stopped with {', '.join(drift)}. The agent's approved scope or the policy changed after "
            "approval, so nothing was read or proposed under the old approval."
        ),
        object_type="agent_run",
        object_ref=run["run_id"],
        href=f"/admin/agents/readiness/{agent_id}/runs/{run['run_id']}",
        recommended_action="Review the change, then dry-run and approve the setup again before the next run.",
        is_task=True,
        mandatory=True,
        # every run the same drift stops folds into one live alert per agent
        correlation_key=f"{POLICY_DRIFT_ALERT}:{agent_id}",
    )
    raised = await _raise(conn, run["tenant_id"], draft)
    run_log(
        run,
        "stopped by policy drift codes=%s alert=%s",
        ",".join(drift),
        raised,
        level=logging.WARNING,
    )
    return raised


async def alert_integrity(
    conn: AsyncConnection, run: dict[str, Any], problems: list[str]
) -> bool | None:
    """Raise the integrity alert when a run's evidence chain does not verify. None when it verifies."""
    if not problems:
        return None
    draft = AlertDraft(
        severity=Severity.CRITICAL,
        kind=EVIDENCE_INTEGRITY_ALERT,
        title="An access readiness evidence chain did not verify",
        body=(
            f"The evidence chain of run {run['run_id']} has {len(problems)} integrity exception(s): "
            f"{', '.join(problems[:5])}. Do not rely on this run's evidence until it is investigated."
        ),
        object_type="agent_run",
        object_ref=run["run_id"],
        href=f"/admin/agents/readiness/{run['agent_principal_id']}/runs/{run['run_id']}",
        recommended_action="Investigate the evidence store for this run; treat its result as unverified.",
        is_task=True,
        mandatory=True,
        correlation_key=f"{EVIDENCE_INTEGRITY_ALERT}:{run['run_id']}",  # each later read of the same broken chain folds in
    )
    raised = await _raise(conn, run["tenant_id"], draft)
    run_log(
        run,
        "evidence chain integrity exceptions=%d alert=%s",
        len(problems),
        raised,
        level=logging.ERROR,
    )
    return raised


async def chain_problems(
    conn: AsyncConnection, run: dict[str, Any], envelopes: list[dict[str, Any]]
) -> list[str]:
    """The run's evidence chain replayed now (AT-20). Integrity exceptions are returned as before and also raise the mandatory alert."""
    problems = store.verify_chain(envelopes)
    await alert_integrity(conn, run, problems)
    return problems
