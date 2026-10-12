"""Agents, blueprints, workload bindings and tools in PostgreSQL (ZID-MACHINE-001 §2-3, §12, §15-16, §18).

Credential issuance reads the live binding for the presented workload identity, the logical agent and
the blueprint review state, then applies `agents.credential_blockers`: a revoked blueprint, a
suspended agent or binding, or an unapproved artifact never gets a fresh credential.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.machine.domain.agents import (
    ActivationEvidence,
    AgentProfile,
    AgentStatus,
    Attestation,
    BindingStatus,
    BlueprintRelease,
    CredentialRequest,
    MachineError,
    MachineState,
    ReviewStatus,
    SideEffect,
    Tool,
    ToolStatus,
    WorkloadBinding,
    agent_transition,
    credential_blockers,
)


async def save_blueprint(conn: AsyncConnection, tenant_id: str, bp: BlueprintRelease) -> None:
    await conn.execute(
        text(
            "INSERT INTO agent.blueprint_release (tenant_id, blueprint_id, version, artifact_digest, "
            "tool_manifest_digest, publisher_principal_id, review_status) VALUES (:t, :b, :v, :a, :tm, :p, :r)"
        ),
        {
            "t": uuid.UUID(tenant_id),
            "b": bp.blueprint_id,
            "v": bp.version,
            "a": bp.artifact_digest,
            "tm": bp.tool_manifest_digest,
            "p": uuid.UUID(bp.publisher_principal_id),
            "r": bp.review.value,
        },
    )


async def set_blueprint_review(
    conn: AsyncConnection, blueprint_id: str, version: str, review: ReviewStatus
) -> None:
    await conn.execute(
        text(
            "UPDATE agent.blueprint_release SET review_status = :r WHERE blueprint_id = :b AND version = :v"
        ),
        {"r": review.value, "b": blueprint_id, "v": version},
    )


async def load_blueprint(
    conn: AsyncConnection, blueprint_id: str, version: str
) -> BlueprintRelease | None:
    row = (
        await conn.execute(
            text("SELECT * FROM agent.blueprint_release WHERE blueprint_id = :b AND version = :v"),
            {"b": blueprint_id, "v": version},
        )
    ).first()
    if row is None:
        return None
    m = row._mapping
    return BlueprintRelease(
        m["blueprint_id"],
        m["version"],
        m["artifact_digest"],
        m["tool_manifest_digest"],
        str(m["publisher_principal_id"]),
        ReviewStatus(m["review_status"]),
    )


def _uuid(v: str | None) -> uuid.UUID | None:
    return uuid.UUID(v) if v else None


async def save_agent(conn: AsyncConnection, a: AgentProfile) -> None:
    if a.status is AgentStatus.ACTIVE:
        raise MachineError(
            "AGENT_ACTIVATION_EVIDENCE_REQUIRED", "insert as DRAFT, then activate with evidence"
        )
    await conn.execute(
        text("""
            INSERT INTO agent.agent_profile (agent_principal_id, tenant_id, agent_name, blueprint_id, blueprint_version,
                publisher_principal_id, operator_principal_id, sponsor_principal_id, owning_organization_id,
                parent_agent_principal_id, model_provider, allowed_tools, max_subagent_depth, status, security_revision)
            VALUES (:id, :t, :n, :b, :v, :pub, :op, :sp, :org, :parent, :mp, :tools, :depth,
                    CAST(:st AS agent.agent_status), :rev)
        """),
        {
            "id": uuid.UUID(a.agent_principal_id),
            "t": uuid.UUID(a.tenant_id),
            "n": a.agent_name,
            "b": a.blueprint_id,
            "v": a.blueprint_version,
            "pub": uuid.UUID(a.publisher_principal_id),
            "op": uuid.UUID(a.operator_principal_id),
            "sp": _uuid(a.sponsor_principal_id),
            "org": _uuid(a.owning_organization_id),
            "parent": _uuid(a.parent_agent_id),
            "mp": a.model_provider,
            "tools": sorted(a.allowed_tools),
            "depth": a.max_subagent_depth,
            "st": a.status.value,
            "rev": a.security_revision,
        },
    )


def _agent(row: Row[Any]) -> AgentProfile:
    m = row._mapping
    return AgentProfile(
        agent_principal_id=str(m["agent_principal_id"]),
        tenant_id=str(m["tenant_id"]),
        agent_name=m["agent_name"],
        blueprint_id=m["blueprint_id"],
        blueprint_version=m["blueprint_version"],
        publisher_principal_id=str(m["publisher_principal_id"]),
        operator_principal_id=str(m["operator_principal_id"]),
        sponsor_principal_id=str(m["sponsor_principal_id"]) if m["sponsor_principal_id"] else None,
        owning_organization_id=str(m["owning_organization_id"])
        if m["owning_organization_id"]
        else None,
        model_provider=m["model_provider"],
        allowed_tools=frozenset(m["allowed_tools"]),
        max_subagent_depth=m["max_subagent_depth"],
        status=AgentStatus(m["status"]),
        security_revision=m["security_revision"],
        parent_agent_id=str(m["parent_agent_principal_id"])
        if m["parent_agent_principal_id"]
        else None,
    )


async def load_agent(conn: AsyncConnection, agent_id: str) -> AgentProfile | None:
    row = (
        await conn.execute(
            text("SELECT * FROM agent.agent_profile WHERE agent_principal_id = :a"),
            {"a": uuid.UUID(agent_id)},
        )
    ).first()
    return _agent(row) if row else None


async def change_agent_status(
    conn: AsyncConnection,
    agent_id: str,
    target: AgentStatus,
    *,
    evidence: ActivationEvidence | None = None,
) -> AgentProfile:
    current = await load_agent(conn, agent_id)
    if current is None:
        raise MachineError("AGENT_NOT_FOUND")
    if target is AgentStatus.ACTIVE and evidence is not None:
        evidence = ActivationEvidence(  # the blueprint gate reads the stored release, not the caller's claim
            evidence.sponsor_verified,
            await load_blueprint(conn, current.blueprint_id, current.blueprint_version),
            evidence.runtime_binding_policy,
            evidence.security_review_passed,
            evidence.short_lived_credentials_configured,
            evidence.observability_configured,
            evidence.kill_path_tested,
        )
    new = agent_transition(current, target, evidence=evidence)
    await conn.execute(
        text(
            "UPDATE agent.agent_profile SET status = CAST(:s AS agent.agent_status), security_revision = :r, "
            "retired_at = CASE WHEN :s = 'RETIRED' THEN now() ELSE retired_at END WHERE agent_principal_id = :a"
        ),
        {"s": new.status.value, "r": new.security_revision, "a": uuid.UUID(agent_id)},
    )
    return new


async def save_binding(conn: AsyncConnection, tenant_id: str, b: WorkloadBinding) -> None:
    await conn.execute(
        text("""
            INSERT INTO agent.workload_binding (binding_id, tenant_id, logical_principal_id, workload_identity_id,
                blueprint_version, artifact_digest, environment_id, attestation_required, status, valid_from, valid_to)
            VALUES (:id, :t, :a, :w, :bv, :d, :env, :att, :st, :vf, :vt)
        """),
        {
            "id": uuid.UUID(b.binding_id),
            "t": uuid.UUID(tenant_id),
            "a": uuid.UUID(b.logical_principal_id),
            "w": b.workload_identity_id,
            "bv": b.blueprint_version,
            "d": b.artifact_digest,
            "env": b.environment_id,
            "att": int(b.attestation_required),
            "st": b.status.value,
            "vf": b.valid_from,
            "vt": b.valid_to,
        },
    )


async def set_binding_status(
    conn: AsyncConnection, workload_identity_id: str, status: BindingStatus
) -> int:
    result = await conn.execute(
        text(
            "UPDATE agent.workload_binding SET status = :s, security_revision = security_revision + 1 "
            "WHERE workload_identity_id = :w AND status = 'ACTIVE'"
        ),
        {"s": status.value, "w": workload_identity_id},
    )
    return int(result.rowcount)


async def save_tool(conn: AsyncConnection, tenant_id: str, tool: Tool) -> None:
    await conn.execute(
        text("""
            INSERT INTO agent.tool_capability (tenant_id, tool_id, version, server_principal_id, canonical_action,
                default_risk_class, side_effect, requires_mandate, requires_human_approval, status)
            VALUES (:t, :id, :v, :srv, :act, :risk, :se, :rm, :rha, :st)
        """),
        {
            "t": uuid.UUID(tenant_id),
            "id": tool.tool_id,
            "v": tool.version,
            "srv": uuid.UUID(tool.server_principal_id),
            "act": tool.canonical_action,
            "risk": tool.default_risk_class,
            "se": tool.side_effect.value,
            "rm": tool.requires_mandate,
            "rha": tool.requires_human_approval,
            "st": tool.status.value,
        },
    )


async def load_tools(conn: AsyncConnection) -> dict[str, Tool]:
    rows = (await conn.execute(text("SELECT * FROM agent.tool_capability"))).all()
    return {
        r.tool_id: Tool(
            r.tool_id,
            r.version,
            str(r.server_principal_id),
            r.canonical_action,
            r.default_risk_class,
            SideEffect(r.side_effect),
            r.requires_mandate,
            r.requires_human_approval,
            ToolStatus(r.status),
        )
        for r in rows
    }


async def credential_check(
    conn: AsyncConnection,
    req: CredentialRequest,
    *,
    quarantined_environments: frozenset[str] = frozenset(),
) -> list[str]:
    """Issuance decision from stored state (MACH-03/04/09/10/11/12/20)."""
    row = (
        await conn.execute(
            text(
                "SELECT * FROM agent.workload_binding WHERE workload_identity_id = :w AND status = 'ACTIVE'"
            ),
            {"w": req.workload_identity_id},
        )
    ).first()
    if row is None:
        return ["WORKLOAD_IDENTITY_UNREGISTERED"]
    m = row._mapping
    binding = WorkloadBinding(
        str(m["binding_id"]),
        str(m["logical_principal_id"]),
        m["workload_identity_id"],
        m["blueprint_version"],
        m["artifact_digest"],
        m["environment_id"],
        Attestation(m["attestation_required"]),
        BindingStatus(m["status"]),
        m["valid_from"],
        m["valid_to"],
    )
    agent = await load_agent(conn, binding.logical_principal_id)
    revoked = (
        (
            await conn.execute(
                text(
                    "SELECT artifact_digest FROM agent.blueprint_release WHERE review_status = 'REVOKED'"
                )
            )
        )
        .scalars()
        .all()
    )
    state = MachineState(
        quarantined_environments=quarantined_environments,
        revoked_blueprint_digests=frozenset(revoked),
    )
    return credential_blockers(req, binding, agent, state)
