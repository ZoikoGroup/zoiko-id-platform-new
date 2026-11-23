"""A live proof of what runs an API-bound agent, for work the platform itself runs on the agent's behalf (CR-142).

An agent bound to an API client has no container for an attestor to look at, so it can never hold a workload attestation. What it can prove is that
it holds a key (CR-093, CR-094), and the platform caps that proof at `API_ATTESTATION_CAP` (level 1). A run that the platform executes in process
(the access readiness agent) asks the same question the token path asks, through the same decision (`credential_problems`: agent active, not frozen,
binding live and in date, client build approved and not revoked, release unchanged), and may count a bound client as its proof at that level and no
higher. Nothing here issues a bearer token or reads a key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform import api_agents_switch
from zid_platform.modules.machine.application.api_agent_token import credential_problems
from zid_platform.modules.machine.domain.api_agents import API_ATTESTATION_CAP
from zid_platform.modules.machine.infrastructure import agent_store, build_store

LEVEL = API_ATTESTATION_CAP  # what a key can show, never more


@dataclass(frozen=True, slots=True)
class ApiProof:
    binding_id: str
    client_id: str
    descriptor_digest: str
    security_revision: int


async def api_proof(
    conn: AsyncConnection, agent_id: str, now: datetime
) -> tuple[ApiProof | None, list[str]]:
    """The agent's API binding as a proof, and why it cannot be one now. `(None, [...])` when the agent has no API binding at all, a proof together
    with problems when it has one that no longer holds, and `(proof, [])` only when every check the token path makes passes."""
    if not api_agents_switch.enabled():
        return None, [
            "API_AGENTS_DISABLED"
        ]  # a bound client is not a proof while the feature is off (CR-144)
    binding = await agent_store.api_binding(conn, agent_id)
    if binding is None:
        return None, ["WORKLOAD_IDENTITY_UNREGISTERED"]
    build = await build_store.get(conn, str(binding["build_id"]))
    problems = await credential_problems(conn, agent_id, binding, build, now)
    proof = ApiProof(
        binding_id=str(binding["binding_id"]),
        client_id=str(binding["client_id"]),
        descriptor_digest=str(binding["descriptor_digest"]),
        security_revision=int(binding["security_revision"]),
    )
    return proof, problems
