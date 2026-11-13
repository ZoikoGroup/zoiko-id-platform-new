"""A read-only account of an agent's identity, for a reviewer that must not change it (CR-091; ZID-AG-WF-001 section 09).

Everything an access readiness run needs to decide whether an agent may act: its lifecycle status, whether its accountable sponsor is
still active, whether its blueprint is approved, and whether a live workload binding holds a verified, unexpired attestation at the level
the binding requires. Fails closed: whatever cannot be read says no.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.machine.application.agents import sponsor_is_active
from zid_platform.modules.machine.domain.agents import ReviewStatus
from zid_platform.modules.machine.infrastructure import machine_store


@dataclass(frozen=True, slots=True)
class AgentIdentityStatus:
    agent_principal_id: str
    status: str
    sponsor_principal_id: str | None
    sponsor_active: bool
    expires_at: datetime | None
    blueprint_id: str
    blueprint_version: str
    blueprint_approved: bool
    allowed_tools: frozenset[str]
    live_bindings: int
    attested: bool
    attestation_level: int
    attestation_id: str | None
    security_revision: int


async def attestation_level(
    conn: AsyncConnection, agent_id: str, now: datetime
) -> tuple[int, str | None]:
    """The highest verified attestation level any live binding of the agent holds, and that attestation's id. A binding counts only if
    its attestation is VALID, unexpired, for the same workload, environment and artifact, and at least the level the binding requires."""
    row = (
        await conn.execute(
            text(
                "SELECT a.level, a.attestation_id FROM agent.workload_binding b "
                "JOIN agent.workload_attestation a ON a.workload_identity_id = b.workload_identity_id "
                "AND a.environment_id = b.environment_id AND a.artifact_digest = b.artifact_digest "
                "WHERE b.logical_principal_id = :a AND b.status = 'ACTIVE' AND b.valid_from <= :now AND b.valid_to > :now "
                "AND a.status = 'VALID' AND a.valid_until > :now AND a.level >= GREATEST(b.attestation_required, 1) "
                "ORDER BY a.level DESC, a.verified_at DESC LIMIT 1"
            ),
            {"a": uuid.UUID(agent_id), "now": now},
        )
    ).first()
    return (int(row.level), str(row.attestation_id)) if row else (0, None)


async def agent_identity_status(
    conn: AsyncConnection, agent_id: str, *, now: datetime
) -> AgentIdentityStatus | None:
    """None when this tenant has no such agent (RLS makes another tenant's agent the same answer)."""
    agent = await machine_store.load_agent(conn, agent_id)
    if agent is None:
        return None
    blueprint = await machine_store.load_blueprint(
        conn, agent.blueprint_id, agent.blueprint_version
    )
    level, attestation_id = await attestation_level(conn, agent_id, now)
    live = int(
        (
            await conn.execute(
                text(
                    "SELECT count(*) FROM agent.workload_binding WHERE logical_principal_id = :a "
                    "AND status = 'ACTIVE' AND valid_from <= :now AND valid_to > :now"
                ),
                {"a": uuid.UUID(agent_id), "now": now},
            )
        ).scalar_one()
    )
    return AgentIdentityStatus(
        agent_principal_id=agent_id,
        status=str(agent.status.value),
        sponsor_principal_id=agent.sponsor_principal_id,
        sponsor_active=await sponsor_is_active(conn, agent.sponsor_principal_id),
        expires_at=agent.expires_at,
        blueprint_id=agent.blueprint_id,
        blueprint_version=agent.blueprint_version,
        blueprint_approved=blueprint is not None and blueprint.review is ReviewStatus.APPROVED,
        allowed_tools=agent.allowed_tools,
        live_bindings=live,
        attested=level >= 1,
        attestation_level=level,
        attestation_id=attestation_id,
        security_revision=agent.security_revision,
    )
