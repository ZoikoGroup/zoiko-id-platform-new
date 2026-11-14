"""Public Protocol interface of the machine module. Other modules import ONLY this.

Workload identity (DEC-F7; F-W6):
- `resolve_workload(conn, spiffe_id=<mesh-verified SpiffeId>, environment_id=..., artifact_digest=...)`
  -> WorkloadPrincipal (the logical principal it may act as) or MachineError with every blocker.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.machine.application.agents import lifecycle_sweep
from zid_platform.modules.machine.application.daara import schedule_due as daara_schedule_due
from zid_platform.modules.machine.application.daara import work_queue as daara_work_queue
from zid_platform.modules.machine.application.governance import (
    configure as configure_agent_governance,
)
from zid_platform.modules.machine.application.governance import (
    sweep_all_tenants as governance_sweep,
)
from zid_platform.modules.machine.application.workload import WorkloadPrincipal, resolve_workload
from zid_platform.modules.machine.domain.agents import MachineError

__all__ = [
    "MachineError",
    "WorkloadPrincipal",
    "activate_agent",
    "agent_status",
    "configure_agent_governance",
    "daara_schedule_due",
    "daara_work_queue",
    "governance_sweep",
    "lifecycle_sweep",
    "resolve_workload",
]


async def agent_status(conn: AsyncConnection, agent_id: str) -> str | None:
    """An agent's lifecycle status, or None if this tenant has no such agent (approval cases check it before they are requested)."""
    from zid_platform.modules.machine.infrastructure import machine_store

    agent = await machine_store.load_agent(conn, agent_id)
    return None if agent is None else str(agent.status.value)


async def activate_agent(
    conn: AsyncConnection, tenant_id: str, agent_id: str, *, actor: str
) -> None:
    """Activate an agent. An approved case calls this; the registry classifies the grant of agent authority as A2."""
    from zid_platform.modules.machine.application.agents import activate

    await activate(conn, tenant_id, agent_id, actor=actor)
