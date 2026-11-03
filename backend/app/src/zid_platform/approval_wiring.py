"""Composition-root wiring of approval executors that need other modules (slice L-10, CR-047).

The tenant module owns the approval service and must not import the machine module (they would join a dependency cycle), so the executor
that activates an agent is registered here, where both are already composed. A process that does not import this module (a job that
uses the service without the app) cannot run an agent case: the case is refused as not executable, which is the safe direction.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.machine.ports import MachineError, activate_agent, agent_status
from zid_platform.modules.tenant.ports import (
    ApprovalError,
    ExecContext,
    Executor,
    Gate,
    register_executor,
)


def _agent(payload: dict[str, Any]) -> str:
    v = payload.get("agent_id")
    if not isinstance(v, str) or not v.strip() or len(v) > 128:
        raise ApprovalError("APPROVAL_PAYLOAD_INVALID", "agent_id is required")
    return v.strip()


async def _validate(conn: AsyncConnection, tenant_id: str, payload: dict[str, Any]) -> None:
    status = await agent_status(conn, _agent(payload))
    if status is None:
        raise ApprovalError("APPROVAL_PAYLOAD_INVALID", "no such agent in this organization")
    if status != "PENDING_APPROVAL":
        raise ApprovalError(
            "APPROVAL_PAYLOAD_INVALID",
            f"the agent is {status}; only a pending agent is activated by approval",
        )


async def _check(conn: AsyncConnection, tenant_id: str, payload: dict[str, Any]) -> list[Gate]:
    status = await agent_status(conn, _agent(payload))
    return [
        Gate(
            "AGENT_PENDING_APPROVAL",
            status == "PENDING_APPROVAL",
            "The agent must still be waiting for approval.",
        )
    ]


async def _run(ctx: ExecContext) -> dict[str, Any]:
    """Activation re-runs the agent registry's own evidence checks (blueprint, scopes, key restrictions, human accountability); if any
    fails the run fails and the agent stays pending. The approval authorizes the instruction, it proves nothing the registry does not."""
    agent = _agent(ctx.case.payload)
    try:
        await activate_agent(ctx.conn, ctx.tenant_id, agent, actor=ctx.case.requester_principal_id)
    except MachineError as e:
        raise ApprovalError(e.code) from e
    return {"agent_id": agent, "status": "ACTIVE"}


register_executor(Executor("lifecycle.agent_activate", _run, _validate, check=_check))
