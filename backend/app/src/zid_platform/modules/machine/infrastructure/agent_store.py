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


def _norm(digest: str) -> str:
    """`sha-256:<hex>` and `sha256:<hex>` are one digest; lookups match either spelling of what is stored."""
    return "sha256:" + digest.split(":", 1)[1].lower()


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
    """Live runtime bindings: a workload (container) binding or an API client binding. Either satisfies the runtime gate."""
    workload = int(
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
    api = int(
        (
            await conn.execute(
                text(
                    "SELECT count(*) FROM agent.api_client_binding WHERE agent_principal_id = :a "
                    "AND status = 'ACTIVE' AND valid_to > now()"
                ),
                {"a": uuid.UUID(agent_id)},
            )
        ).scalar_one()
    )
    return workload + api


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
    api = await conn.execute(
        text(
            "UPDATE agent.api_client_binding SET status = :to, security_revision = security_revision + 1 "
            "WHERE agent_principal_id = ANY(:ids) AND status = ANY(:from)"
        ),
        {"to": to, "ids": [uuid.UUID(i) for i in agent_ids], "from": list(from_status)},
    )
    return int(result.rowcount) + int(api.rowcount)


async def suspend_bindings_for_digest(conn: AsyncConnection, artifact_digest: str) -> int:
    """A compromised blueprint artifact quarantines every live binding that runs it (§3.1)."""
    result = await conn.execute(
        text(
            "UPDATE agent.workload_binding SET status = 'SUSPENDED', security_revision = security_revision + 1 "
            "WHERE replace(lower(artifact_digest), 'sha-256:', 'sha256:') = :d AND status = 'ACTIVE'"
        ),
        {"d": _norm(artifact_digest)},
    )
    api = await conn.execute(
        text(
            "UPDATE agent.api_client_binding SET status = 'SUSPENDED', security_revision = security_revision + 1 "
            "WHERE descriptor_digest = :d AND status = 'ACTIVE'"
        ),
        {"d": _norm(artifact_digest)},
    )
    return int(result.rowcount) + int(api.rowcount)


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


async def agents_using_digest(conn: AsyncConnection, artifact_digest: str) -> list[str]:
    """Live agents whose blueprint release runs this artifact (a revoked build reaches them, §3.1)."""
    rows = (
        await conn.execute(
            text(
                "SELECT a.agent_principal_id FROM agent.agent_profile a JOIN agent.blueprint_release b "
                "ON b.blueprint_id = a.blueprint_id AND b.version = a.blueprint_version "
                "WHERE replace(lower(b.artifact_digest), 'sha-256:', 'sha256:') = :d AND a.status IN ('ACTIVE', 'PENDING_APPROVAL')"
            ),
            {"d": _norm(artifact_digest)},
        )
    ).all()
    return [str(r.agent_principal_id) for r in rows]


async def revoke_blueprints_for_digest(conn: AsyncConnection, artifact_digest: str) -> int:
    """A revoked build can no longer back a release: every draft or approved release of it is revoked."""
    result = await conn.execute(
        text(
            "UPDATE agent.blueprint_release SET review_status = 'REVOKED' "
            "WHERE replace(lower(artifact_digest), 'sha-256:', 'sha256:') = :d AND review_status IN ('DRAFT', 'APPROVED')"
        ),
        {"d": _norm(artifact_digest)},
    )
    return int(result.rowcount)


# ---------------------------------------------------------------- API client bindings (CR-093)


async def insert_api_binding(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    binding_id: str,
    agent_id: str,
    build_id: str,
    client_id: str,
    descriptor_digest: str,
    valid_to: Any,
    created_by: str,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO agent.api_client_binding (binding_id, tenant_id, agent_principal_id, build_id, client_id, descriptor_digest, "
            "status, valid_to, created_by) VALUES (:b, :t, :a, :bd, :c, :d, 'ACTIVE', :v, :by)"
        ),
        {
            "b": uuid.UUID(binding_id), "t": uuid.UUID(tenant_id), "a": uuid.UUID(agent_id), "bd": uuid.UUID(build_id),
            "c": client_id, "d": descriptor_digest, "v": valid_to, "by": uuid.UUID(created_by),
        },
    )  # fmt: skip


async def api_binding(conn: AsyncConnection, agent_id: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM agent.api_client_binding WHERE agent_principal_id = :a ORDER BY created_at DESC LIMIT 1"
            ),
            {"a": uuid.UUID(agent_id)},
        )
    ).first()
    return dict(row._mapping) if row else None


async def api_bound_agents(conn: AsyncConnection, agent_ids: list[str]) -> set[str]:
    """Which of these agents hold an API client binding that is not revoked. Used to cap what an API-bound agent may hold."""
    if not agent_ids:
        return set()
    rows = (
        await conn.execute(
            text(
                "SELECT agent_principal_id FROM agent.api_client_binding "
                "WHERE agent_principal_id = ANY(CAST(:ids AS uuid[])) AND status IN ('ACTIVE', 'SUSPENDED')"
            ),
            {"ids": [uuid.UUID(i) for i in agent_ids]},
        )
    ).all()
    return {str(r[0]) for r in rows}


async def revoke_api_binding(conn: AsyncConnection, agent_id: str, reason: str) -> bool:
    row = (
        await conn.execute(
            text(
                "UPDATE agent.api_client_binding SET status = 'REVOKED', revoked_at = now(), revoke_reason = :why, "
                "security_revision = security_revision + 1 WHERE agent_principal_id = :a AND status IN ('ACTIVE', 'SUSPENDED') "
                "RETURNING binding_id"
            ),
            {"a": uuid.UUID(agent_id), "why": reason[:200]},
        )
    ).first()
    return row is not None


async def api_binding_by_client(conn: AsyncConnection, client_id: str) -> dict[str, Any] | None:
    """The newest binding of a client id inside the current tenant (row-level security scopes it)."""
    row = (
        await conn.execute(
            text(
                "SELECT * FROM agent.api_client_binding WHERE client_id = :c ORDER BY created_at DESC LIMIT 1"
            ),
            {"c": client_id},
        )
    ).first()
    return dict(row._mapping) if row else None


async def claim_api_assertion(
    conn: AsyncConnection, tenant_id: str, *, jti: str, binding_id: str, expires_at: Any
) -> bool:
    """Burn an assertion id. False when it was already used: a replay. Expired ids are swept first, a few at a time."""
    await conn.execute(
        text(
            "DELETE FROM agent.api_assertion_jti WHERE ctid IN "
            "(SELECT ctid FROM agent.api_assertion_jti WHERE expires_at < now() - interval '1 hour' LIMIT 50)"
        )
    )
    row = (
        await conn.execute(
            text(
                "INSERT INTO agent.api_assertion_jti (tenant_id, jti, binding_id, expires_at) VALUES (:t, :j, :b, :e) "
                "ON CONFLICT (tenant_id, jti) DO NOTHING RETURNING jti"
            ),
            {"t": uuid.UUID(tenant_id), "j": jti, "b": uuid.UUID(binding_id), "e": expires_at},
        )
    ).first()
    return row is not None
