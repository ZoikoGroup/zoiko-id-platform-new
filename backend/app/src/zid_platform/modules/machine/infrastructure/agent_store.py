"""Reads, gate attestations and compromise cascades for the agent registry (tenant transactions, RLS on).

The writes of single agents, blueprints and tools live in `machine_store`; this module adds what the
registry needs on top: listing, the recorded human attestations, finding an agent's descendants, and
suspending the workload bindings an agent, blueprint or tool compromise reaches (ZID-MACHINE-001 §18).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.machine.domain.agents import ToolStatus


def _iso(v: Any) -> str | None:
    return v.isoformat() if v is not None else None


async def list_agents(
    conn: AsyncConnection, *, status: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    where, params = "", {"n": limit}
    if status:
        where, params["s"] = "WHERE status = CAST(:s AS agent.agent_status)", status  # type: ignore[assignment]
    rows = (
        await conn.execute(
            text(
                "SELECT agent_principal_id, agent_name, blueprint_id, blueprint_version, publisher_principal_id, "
                "operator_principal_id, sponsor_principal_id, owning_organization_id, parent_agent_principal_id, "
                "model_provider, allowed_tools, max_subagent_depth, status, security_revision, created_at, retired_at "
                f"FROM agent.agent_profile {where} ORDER BY created_at DESC LIMIT :n"
            ),
            params,
        )
    ).all()
    return [
        {
            "agent_principal_id": str(r.agent_principal_id),
            "agent_name": r.agent_name,
            "blueprint_id": r.blueprint_id,
            "blueprint_version": r.blueprint_version,
            "publisher_principal_id": str(r.publisher_principal_id),
            "operator_principal_id": str(r.operator_principal_id),
            "sponsor_principal_id": str(r.sponsor_principal_id) if r.sponsor_principal_id else None,
            "owning_organization_id": str(r.owning_organization_id)
            if r.owning_organization_id
            else None,
            "parent_agent_id": str(r.parent_agent_principal_id)
            if r.parent_agent_principal_id
            else None,
            "model_provider": r.model_provider,
            "allowed_tools": sorted(r.allowed_tools),
            "max_subagent_depth": r.max_subagent_depth,
            "status": r.status,
            "security_revision": r.security_revision,
            "created_at": _iso(r.created_at),
            "retired_at": _iso(r.retired_at),
        }
        for r in rows
    ]


async def list_blueprints(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT blueprint_id, version, artifact_digest, tool_manifest_digest, publisher_principal_id, "
                "review_status, effective_at FROM agent.blueprint_release ORDER BY blueprint_id, effective_at DESC"
            )
        )
    ).all()
    return [
        {
            "blueprint_id": r.blueprint_id,
            "version": r.version,
            "artifact_digest": r.artifact_digest,
            "tool_manifest_digest": r.tool_manifest_digest,
            "publisher_principal_id": str(r.publisher_principal_id),
            "review_status": r.review_status,
            "effective_at": _iso(r.effective_at),
        }
        for r in rows
    ]


async def set_tool_status(conn: AsyncConnection, tool_id: str, status: ToolStatus) -> bool:
    result = await conn.execute(
        text("UPDATE agent.tool_capability SET status = :s WHERE tool_id = :t"),
        {"s": status.value, "t": tool_id},
    )
    return result.rowcount == 1


async def attest(
    conn: AsyncConnection,
    tenant_id: str,
    agent_id: str,
    gate: str,
    *,
    by: str,
    note: str,
    now: datetime,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO agent.agent_gate_attestation (agent_principal_id, tenant_id, gate, attested_by, note, "
            "attested_at) VALUES (:a, :t, :g, :by, :n, :now) ON CONFLICT (agent_principal_id, gate) DO UPDATE "
            "SET attested_by = EXCLUDED.attested_by, note = EXCLUDED.note, attested_at = EXCLUDED.attested_at"
        ),
        {
            "a": uuid.UUID(agent_id),
            "t": uuid.UUID(tenant_id),
            "g": gate,
            "by": uuid.UUID(by),
            "n": note,
            "now": now,
        },
    )


async def attestations(conn: AsyncConnection, agent_id: str) -> dict[str, dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT gate, attested_by, note, attested_at FROM agent.agent_gate_attestation "
                "WHERE agent_principal_id = :a"
            ),
            {"a": uuid.UUID(agent_id)},
        )
    ).all()
    return {
        r.gate: {
            "attested_by": str(r.attested_by),
            "note": r.note,
            "attested_at": _iso(r.attested_at),
        }
        for r in rows
    }


async def clear_attestations(conn: AsyncConnection, agent_id: str) -> None:
    """A material change (new blueprint, tool set) means the gates must be attested again."""
    await conn.execute(
        text("DELETE FROM agent.agent_gate_attestation WHERE agent_principal_id = :a"),
        {"a": uuid.UUID(agent_id)},
    )


async def live_binding_count(conn: AsyncConnection, agent_id: str) -> int:
    return int(
        (
            await conn.execute(
                text(
                    "SELECT count(*) FROM agent.workload_binding WHERE logical_principal_id = :a "
                    "AND status = 'ACTIVE' AND valid_to > now()"
                ),
                {"a": uuid.UUID(agent_id)},
            )
        ).scalar_one()
    )


async def descendants(conn: AsyncConnection, agent_id: str) -> list[str]:
    """Every sub-agent below `agent_id`, so a compromise reaches the whole delegation tree (MACH-09)."""
    rows = (
        await conn.execute(
            text(
                "WITH RECURSIVE tree AS (SELECT agent_principal_id FROM agent.agent_profile "
                "WHERE parent_agent_principal_id = :a UNION SELECT p.agent_principal_id FROM agent.agent_profile p "
                "JOIN tree t ON p.parent_agent_principal_id = t.agent_principal_id) SELECT agent_principal_id FROM tree"
            ),
            {"a": uuid.UUID(agent_id)},
        )
    ).all()
    return [str(r.agent_principal_id) for r in rows]


async def move_bindings(
    conn: AsyncConnection,
    *,
    agent_ids: list[str],
    to: str,
    from_status: tuple[str, ...] = ("ACTIVE",),
) -> int:
    """Suspend or revoke the live runtime bindings of these agents. Historical evidence is kept."""
    if not agent_ids:
        return 0
    result = await conn.execute(
        text(
            "UPDATE agent.workload_binding SET status = :to, security_revision = security_revision + 1 "
            "WHERE logical_principal_id = ANY(:ids) AND status = ANY(:from)"
        ),
        {"to": to, "ids": [uuid.UUID(i) for i in agent_ids], "from": list(from_status)},
    )
    return int(result.rowcount)


async def suspend_bindings_for_digest(conn: AsyncConnection, artifact_digest: str) -> int:
    """A compromised blueprint artifact quarantines every live binding that runs it (§3.1)."""
    result = await conn.execute(
        text(
            "UPDATE agent.workload_binding SET status = 'SUSPENDED', security_revision = security_revision + 1 "
            "WHERE artifact_digest = :d AND status = 'ACTIVE'"
        ),
        {"d": artifact_digest},
    )
    return int(result.rowcount)


async def agents_using_blueprint(
    conn: AsyncConnection, blueprint_id: str, version: str
) -> list[str]:
    rows = (
        await conn.execute(
            text(
                "SELECT agent_principal_id FROM agent.agent_profile WHERE blueprint_id = :b AND "
                "blueprint_version = :v AND status IN ('ACTIVE', 'PENDING_APPROVAL')"
            ),
            {"b": blueprint_id, "v": version},
        )
    ).all()
    return [str(r.agent_principal_id) for r in rows]


async def agents_with_tool(conn: AsyncConnection, tool_id: str) -> int:
    return int(
        (
            await conn.execute(
                text(
                    "SELECT count(*) FROM agent.agent_profile WHERE :t = ANY(allowed_tools) "
                    "AND status IN ('ACTIVE', 'PENDING_APPROVAL', 'SUSPENDED')"
                ),
                {"t": tool_id},
            )
        ).scalar_one()
    )
