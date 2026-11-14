"""Agent, blueprint and tool registry (ZID-MACHINE-001 §2-3, §12-15, §18; MACH-01..21; CR-021).

Identity, not authority: registering, approving or activating an agent never gives it authority to act
for anyone. Every state change here emits its catalogued event and an audit record, and a compromise
reaches exactly what the spec says it reaches: a suspended agent takes its sub-agents and live workload
bindings with it; a revoked blueprint suspends the bindings that run its artifact and the agents built on it.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_outbox import emit, next_version

from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit
from zid_platform.modules.machine.domain.agents import (
    ATTESTABLE_GATES,
    ActivationEvidence,
    AgentProfile,
    AgentStatus,
    BlueprintRelease,
    MachineError,
    ReviewStatus,
    SideEffect,
    Tool,
    ToolStatus,
    activation_blockers,
    attestor_blockers,
    evidence_from_attestations,
    lifecycle_breach,
    register_child_agent,
    tool_requires_mandate,
)
from zid_platform.modules.machine.infrastructure import agent_store, build_store, machine_store
from zid_platform.modules.organization.ports import organization_exists
from zid_platform.modules.principal.ports import (
    Principal,
    PrincipalStatus,
    PrincipalType,
    create_principal,
    load_principal,
    set_status,
)

DIGEST = re.compile(r"^sha-?256:[0-9a-f]{64}$")
ACTION_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
SPONSOR_TYPES = (PrincipalType.HUMAN, PrincipalType.ORGANIZATION)
SERVER_TYPES = (PrincipalType.APPLICATION, PrincipalType.SERVICE)


def _now() -> datetime:
    return datetime.now(UTC)


async def _record(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    name: str,
    event: bool,
    aggregate_type: str,
    aggregate_id: str,
    actor: str | None,
    data: dict[str, Any],
    evidence: EvidenceClass = EvidenceClass.E3,
    actor_type: str = "HUMAN",
    outcome: str = "SUCCESS",
    event_aggregate_id: str | None = None,
) -> None:
    event_id = None
    if event:
        # The outbox keys an event on (type, id, version) across the whole platform, and the version is counted per company. An id a person
        # chose (a tool id) can be the same in two companies, so such events carry a company-scoped id; the audit record keeps the plain one.
        key = event_aggregate_id or aggregate_id
        event_id = await emit(
            conn,
            name=name,
            tenant_id=tenant_id,
            aggregate_type=aggregate_type,
            aggregate_id=key,
            aggregate_version=await next_version(conn, aggregate_type, key),
            data={**data, "evidence_class": evidence.value},
        )
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type=name,
            actor_type=actor_type,
            action=name.rsplit(".", 1)[-1],
            target_type=aggregate_type,
            target_ref=aggregate_id,
            outcome=outcome,
            evidence_class=evidence,
            occurred_at=_now(),
            actor_principal_id=actor,
            source_service="machine",
            source_event_id=event_id,
            details={k: v for k, v in data.items() if isinstance(v, (str, int, bool))},
        ),
    )


def profile_view(a: AgentProfile) -> dict[str, Any]:
    """Publisher, operator, sponsor and owning organization stay separate fields (UX-11, §2.1)."""
    return {
        "agent_principal_id": a.agent_principal_id,
        "agent_name": a.agent_name,
        "blueprint_id": a.blueprint_id,
        "blueprint_version": a.blueprint_version,
        "publisher_principal_id": a.publisher_principal_id,
        "operator_principal_id": a.operator_principal_id,
        "sponsor_principal_id": a.sponsor_principal_id,
        "owning_organization_id": a.owning_organization_id,
        "parent_agent_id": a.parent_agent_id,
        "model_provider": a.model_provider,
        "allowed_tools": sorted(a.allowed_tools),
        "max_subagent_depth": a.max_subagent_depth,
        "status": a.status.value,
        "security_revision": a.security_revision,
        "expires_at": a.expires_at.isoformat() if a.expires_at else None,
    }


def tool_view(t: Tool) -> dict[str, Any]:
    return {
        "tool_id": t.tool_id,
        "version": t.version,
        "server_principal_id": t.server_principal_id,
        "canonical_action": t.canonical_action,
        "default_risk_class": t.default_risk_class,
        "side_effect": t.side_effect.value,
        "requires_mandate": tool_requires_mandate(t),
        "requires_human_approval": t.requires_human_approval,
        "status": t.status.value,
        # discovery never implies execution: only ACTIVE tools can be invoked (UX-12, MACH-15)
        "executable": t.status is ToolStatus.ACTIVE,
    }


async def _principal(conn: AsyncConnection, principal_id: str) -> Principal | None:
    try:
        principal, _version = await load_principal(conn, principal_id)
    except Exception:  # noqa: BLE001 - a missing principal is a domain answer, not a crash
        return None
    return principal


async def _set_principal(conn: AsyncConnection, principal_id: str, target: PrincipalStatus) -> None:
    current = await _principal(conn, principal_id)
    if current is None or current.status is target:
        return
    try:
        await set_status(conn, principal_id, target)
    except Exception as e:
        raise MachineError("AGENT_PRINCIPAL_STATE", str(e)) from e


async def _load(conn: AsyncConnection, agent_id: str) -> AgentProfile:
    agent = await machine_store.load_agent(conn, agent_id)
    if agent is None:
        raise MachineError("AGENT_NOT_FOUND")
    return agent


# ---------------------------------------------------------------- blueprints (§3)


async def create_blueprint(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    blueprint_id: str,
    version: str,
    artifact_digest: str,
    tool_manifest_digest: str,
    publisher_principal_id: str,
    actor: str,
    require_approved_build: bool = False,
) -> BlueprintRelease:
    if not DIGEST.match(artifact_digest) or not DIGEST.match(tool_manifest_digest):
        raise MachineError("WORKLOAD_ARTIFACT_NOT_APPROVED", "digests must be sha256:<64 hex>")
    if require_approved_build:
        await require_build_approved(conn, artifact_digest)
    artifact_digest = normalize_digest(
        artifact_digest
    )  # one spelling is stored, so the same build always matches
    if await machine_store.load_blueprint(conn, blueprint_id, version) is not None:
        raise MachineError("BLUEPRINT_EXISTS", "a release is immutable; publish a new version")
    bp = BlueprintRelease(
        blueprint_id,
        version,
        artifact_digest,
        tool_manifest_digest,
        publisher_principal_id,
        ReviewStatus.DRAFT,
    )
    await machine_store.save_blueprint(conn, tenant_id, bp)
    await _record(
        conn,
        tenant_id,
        name="agent_registry.blueprint_drafted",
        event=False,
        aggregate_type="agent_blueprint",
        aggregate_id=f"{tenant_id}:{blueprint_id}@{version}",
        actor=actor,
        data={"blueprint_id": blueprint_id, "version": version},
        evidence=EvidenceClass.E2,
    )
    return bp


async def approve_blueprint(
    conn: AsyncConnection, tenant_id: str, blueprint_id: str, version: str, *, actor: str
) -> BlueprintRelease:
    bp = await machine_store.load_blueprint(conn, blueprint_id, version)
    if bp is None:
        raise MachineError("BLUEPRINT_NOT_FOUND")
    if bp.review is not ReviewStatus.DRAFT:
        raise MachineError(
            "BLUEPRINT_INVALID_STATE", f"a {bp.review.value} release cannot be approved"
        )
    if actor == bp.publisher_principal_id:
        raise MachineError(
            "AGENT_REVIEW_NOT_INDEPENDENT", "the publisher cannot approve their own release"
        )
    await machine_store.set_blueprint_review(conn, blueprint_id, version, ReviewStatus.APPROVED)
    await _record(
        conn,
        tenant_id,
        name="agent.blueprint.approved",
        event=True,
        aggregate_type="agent_blueprint",
        aggregate_id=f"{tenant_id}:{blueprint_id}@{version}",
        actor=actor,
        data={
            "blueprint_id": blueprint_id,
            "version": version,
            "artifact_digest": bp.artifact_digest,
        },
    )
    return BlueprintRelease(
        bp.blueprint_id,
        bp.version,
        bp.artifact_digest,
        bp.tool_manifest_digest,
        bp.publisher_principal_id,
        ReviewStatus.APPROVED,
    )


async def revoke_blueprint(
    conn: AsyncConnection,
    tenant_id: str,
    blueprint_id: str,
    version: str,
    *,
    reason: str,
    actor: str,
) -> dict[str, int]:
    """Supply-chain compromise (§3.1): quarantine everything running the artifact, keep the evidence."""
    bp = await machine_store.load_blueprint(conn, blueprint_id, version)
    if bp is None:
        raise MachineError("BLUEPRINT_NOT_FOUND")
    if bp.review is ReviewStatus.REVOKED:
        raise MachineError("BLUEPRINT_INVALID_STATE", "already revoked")
    await machine_store.set_blueprint_review(conn, blueprint_id, version, ReviewStatus.REVOKED)
    bindings = await agent_store.suspend_bindings_for_digest(conn, bp.artifact_digest)
    suspended = 0
    for agent_id in await agent_store.agents_using_blueprint(conn, blueprint_id, version):
        agent = await _load(conn, agent_id)
        if agent.status is AgentStatus.ACTIVE:
            await _suspend_one(conn, tenant_id, agent, reason="BLUEPRINT_REVOKED", actor=actor)
            suspended += 1
    await _record(
        conn,
        tenant_id,
        name="agent.blueprint.revoked",
        event=True,
        aggregate_type="agent_blueprint",
        aggregate_id=f"{tenant_id}:{blueprint_id}@{version}",
        actor=actor,
        data={
            "blueprint_id": blueprint_id,
            "version": version,
            "reason_code": reason,
            "bindings_suspended": bindings,
            "agents_suspended": suspended,
        },
    )
    return {"bindings_suspended": bindings, "agents_suspended": suspended}


# ---------------------------------------------------------------- builds (§3, §9; CR-064)


def normalize_digest(digest: str) -> str:
    """`sha-256:<hex>` and `sha256:<hex>` name the same build; builds are stored and matched in the second form."""
    return "sha256:" + digest.split(":", 1)[1].lower()


async def require_build_approved(conn: AsyncConnection, artifact_digest: str) -> dict[str, Any]:
    """A release may run only a build someone other than its registrar approved (and that has not been revoked)."""
    build = await build_store.by_digest(conn, normalize_digest(artifact_digest))
    if build is None:
        raise MachineError(
            "BUILD_NOT_APPROVED",
            "this build is not registered: register it under Builds and have it approved",
        )
    if build["review_status"] != "APPROVED":
        raise MachineError(
            "BUILD_NOT_APPROVED", f"this build is {build['review_status'].lower()}, not approved"
        )
    return build


async def register_build(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    name: str,
    artifact_digest: str,
    source_ref: str | None,
    provenance_ref: str | None,
    actor: str,
) -> dict[str, Any]:
    if not DIGEST.match(artifact_digest):
        raise MachineError("WORKLOAD_ARTIFACT_NOT_APPROVED", "the digest must be sha256:<64 hex>")
    digest = normalize_digest(artifact_digest)
    if await build_store.by_digest(conn, digest) is not None:
        raise MachineError("BUILD_EXISTS", "this build is already registered")
    build_id = str(uuid7())
    await build_store.insert(
        conn,
        tenant_id,
        build_id=build_id,
        name=name.strip(),
        artifact_digest=digest,
        source_ref=source_ref or None,
        provenance_ref=provenance_ref or None,
        registered_by=actor,
    )
    await _record(
        conn,
        tenant_id,
        name="agent_registry.build_registered",
        event=False,
        aggregate_type="agent_build",
        aggregate_id=f"{tenant_id}:{build_id}",
        actor=actor,
        data={
            "build_id": build_id,
            "artifact_digest": digest,
        },  # the label is not recorded: audit data carries no names
        evidence=EvidenceClass.E2,
    )
    built = await build_store.get(conn, build_id)
    assert built is not None
    return built


async def approve_build(
    conn: AsyncConnection, tenant_id: str, build_id: str, *, actor: str
) -> dict[str, Any]:
    build = await build_store.get(conn, build_id, lock=True)
    if build is None:
        raise MachineError("BUILD_NOT_FOUND")
    if build["review_status"] != "DRAFT":
        raise MachineError(
            "BUILD_INVALID_STATE", f"a {build['review_status'].lower()} build cannot be approved"
        )
    if actor == build["registered_by"]:
        raise MachineError(
            "AGENT_REVIEW_NOT_INDEPENDENT", "the person who registered a build cannot approve it"
        )
    await build_store.approve(conn, build_id, actor)
    await _record(
        conn,
        tenant_id,
        name="agent_registry.build_approved",
        event=False,
        aggregate_type="agent_build",
        aggregate_id=f"{tenant_id}:{build_id}",
        actor=actor,
        data={"build_id": build_id, "artifact_digest": build["artifact_digest"]},
    )
    approved = await build_store.get(conn, build_id)
    assert approved is not None
    return approved


async def revoke_build(
    conn: AsyncConnection, tenant_id: str, build_id: str, *, reason: str, actor: str
) -> dict[str, Any]:
    """The build turned out to be bad: nothing may run it. Its releases are revoked, its bindings and live agents suspended."""
    build = await build_store.get(conn, build_id, lock=True)
    if build is None:
        raise MachineError("BUILD_NOT_FOUND")
    if build["review_status"] == "REVOKED":
        raise MachineError("BUILD_INVALID_STATE", "already revoked")
    digest = build["artifact_digest"]
    await build_store.revoke(conn, build_id, reason)
    bindings = await agent_store.suspend_bindings_for_digest(conn, digest)
    suspended = 0
    for agent_id in await agent_store.agents_using_digest(conn, digest):
        agent = await _load(conn, agent_id)
        if agent.status is AgentStatus.ACTIVE:
            await _suspend_one(conn, tenant_id, agent, reason="BUILD_REVOKED", actor=actor)
            suspended += 1
    releases = await agent_store.revoke_blueprints_for_digest(conn, digest)
    await _record(
        conn,
        tenant_id,
        name="agent_registry.build_revoked",
        event=False,
        aggregate_type="agent_build",
        aggregate_id=f"{tenant_id}:{build_id}",
        actor=actor,
        data={
            "build_id": build_id,
            "artifact_digest": digest,
            "reason_code": reason,
            "releases_revoked": releases,
            "bindings_suspended": bindings,
            "agents_suspended": suspended,
        },
    )
    revoked = await build_store.get(conn, build_id)
    assert revoked is not None
    return {
        **revoked,
        "releases_revoked": releases,
        "bindings_suspended": bindings,
        "agents_suspended": suspended,
    }


# ---------------------------------------------------------------- agents (§2, §12)


async def register_agent(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    agent_name: str,
    blueprint_id: str,
    blueprint_version: str,
    operator_principal_id: str,
    sponsor_principal_id: str,
    owning_organization_id: str | None,
    model_provider: str,
    allowed_tools: list[str],
    max_subagent_depth: int,
    home_cell: str,
    actor: str,
    parent_agent_id: str | None = None,
    expires_at: datetime | None = None,
) -> AgentProfile:
    if expires_at is not None and expires_at <= _now():
        raise MachineError("AGENT_EXPIRY_INVALID", "an agent's expiry must be in the future")
    bp = await machine_store.load_blueprint(conn, blueprint_id, blueprint_version)
    if bp is None:
        raise MachineError("BLUEPRINT_NOT_FOUND")
    if bp.review is ReviewStatus.REVOKED:
        raise MachineError("BLUEPRINT_REVOKED")
    registry = await machine_store.load_tools(conn)
    unknown = sorted(set(allowed_tools) - set(registry))
    if unknown:
        raise MachineError("TOOL_NOT_REGISTERED", ", ".join(unknown))
    for pid, what in ((operator_principal_id, "operator"), (sponsor_principal_id, "sponsor")):
        if await _principal(conn, pid) is None:
            raise MachineError(
                "AGENT_PRINCIPAL_NOT_FOUND", f"the {what} is not a principal of this tenant"
            )
    agent_id = str(uuid7())
    agent = AgentProfile(
        agent_principal_id=agent_id,
        tenant_id=tenant_id,
        agent_name=agent_name,
        blueprint_id=blueprint_id,
        blueprint_version=blueprint_version,
        publisher_principal_id=bp.publisher_principal_id,
        operator_principal_id=operator_principal_id,
        sponsor_principal_id=sponsor_principal_id,
        owning_organization_id=owning_organization_id,
        model_provider=model_provider,
        allowed_tools=frozenset(allowed_tools),
        max_subagent_depth=max_subagent_depth,
        status=AgentStatus.DRAFT,
        parent_agent_id=parent_agent_id,
        expires_at=expires_at,
    )
    await create_principal(
        conn,
        Principal(
            agent_id,
            tenant_id,
            PrincipalType.AI_AGENT,
            PrincipalStatus.PENDING,
            home_cell,
            sponsor_principal_id=sponsor_principal_id,
        ),
    )
    await machine_store.save_agent(conn, agent)
    await _record(
        conn,
        tenant_id,
        name="agent.registered",
        event=True,
        aggregate_type="agent",
        aggregate_id=agent_id,
        actor=actor,
        data={
            "agent_principal_id": agent_id,
            "blueprint_id": blueprint_id,
            "blueprint_version": blueprint_version,
        },
    )
    return agent


async def register_child(
    conn: AsyncConnection,
    tenant_id: str,
    parent_id: str,
    *,
    agent_name: str,
    operator_principal_id: str,
    sponsor_principal_id: str,
    allowed_tools: list[str],
    max_subagent_depth: int,
    home_cell: str,
    actor: str,
) -> AgentProfile:
    """§13: the identity service issues the child's identity; the parent never mints it (MACH-17)."""
    parent = await _load(conn, parent_id)
    child = await register_agent(
        conn,
        tenant_id,
        agent_name=agent_name,
        blueprint_id=parent.blueprint_id,
        blueprint_version=parent.blueprint_version,
        operator_principal_id=operator_principal_id,
        sponsor_principal_id=sponsor_principal_id,
        owning_organization_id=parent.owning_organization_id,
        model_provider=parent.model_provider,
        allowed_tools=allowed_tools,
        max_subagent_depth=max_subagent_depth,
        home_cell=home_cell,
        actor=actor,
        parent_agent_id=parent_id,
        expires_at=parent.expires_at,
    )
    problems = register_child_agent(parent, child, issued_by_identity_service=True)
    if problems:
        raise MachineError(problems[0], ", ".join(problems))
    await _record(
        conn,
        tenant_id,
        name="subagent.created",
        event=True,
        aggregate_type="agent",
        aggregate_id=child.agent_principal_id,
        actor=actor,
        data={"agent_principal_id": child.agent_principal_id, "parent_agent_id": parent_id},
    )
    return child


async def submit(
    conn: AsyncConnection, tenant_id: str, agent_id: str, *, actor: str
) -> AgentProfile:
    agent = await machine_store.change_agent_status(conn, agent_id, AgentStatus.PENDING_APPROVAL)
    await _record(
        conn,
        tenant_id,
        name="agent_registry.submitted",
        event=False,
        aggregate_type="agent",
        aggregate_id=agent_id,
        actor=actor,
        data={"agent_principal_id": agent_id},
        evidence=EvidenceClass.E2,
    )
    return agent


async def attest_gate(
    conn: AsyncConnection, tenant_id: str, agent_id: str, gate: str, *, note: str, actor: str
) -> dict[str, Any]:
    agent = await _load(conn, agent_id)
    problems = attestor_blockers(gate, actor, agent)
    if problems:
        raise MachineError(problems[0], ", ".join(problems))
    now = _now()
    await agent_store.attest(conn, tenant_id, agent_id, gate, by=actor, note=note, now=now)
    await _record(
        conn,
        tenant_id,
        name=f"agent_registry.gate_{gate.lower()}",
        event=False,
        aggregate_type="agent",
        aggregate_id=agent_id,
        actor=actor,
        data={"gate": gate},
        evidence=EvidenceClass.E3,
    )
    return {"gate": gate, "attested_by": actor, "note": note, "attested_at": now.isoformat()}


async def _evidence(conn: AsyncConnection, agent: AgentProfile) -> ActivationEvidence:
    sponsor_ok = False
    if agent.sponsor_principal_id and agent.owning_organization_id:
        sponsor = await _principal(conn, agent.sponsor_principal_id)
        sponsor_ok = (
            sponsor is not None
            and sponsor.status is PrincipalStatus.ACTIVE
            and sponsor.principal_type in SPONSOR_TYPES
            and await organization_exists(conn, agent.owning_organization_id)
        )
    attested = await agent_store.attestations(conn, agent.agent_principal_id)
    return evidence_from_attestations(
        agent,
        sponsor_verified=sponsor_ok,
        blueprint=await machine_store.load_blueprint(
            conn, agent.blueprint_id, agent.blueprint_version
        ),
        has_active_binding=await agent_store.live_binding_count(conn, agent.agent_principal_id) > 0,
        attested=attested.keys(),
    )


async def readiness(conn: AsyncConnection, agent_id: str) -> dict[str, Any]:
    """The §12.1 gate list with what is still blocking, for the activation checklist."""
    agent = await _load(conn, agent_id)
    ev = await _evidence(conn, agent)
    return {
        "agent_principal_id": agent_id,
        "status": agent.status.value,
        "blockers": activation_blockers(agent, ev),
        "attestations": await agent_store.attestations(conn, agent_id),
        "attestable_gates": list(ATTESTABLE_GATES),
        "live_bindings": await agent_store.live_binding_count(conn, agent_id),
    }


async def activate(
    conn: AsyncConnection, tenant_id: str, agent_id: str, *, actor: str
) -> AgentProfile:
    agent = await _load(conn, agent_id)
    evidence = await _evidence(conn, agent)
    new = await machine_store.change_agent_status(
        conn, agent_id, AgentStatus.ACTIVE, evidence=evidence
    )
    await _set_principal(conn, agent_id, PrincipalStatus.ACTIVE)
    await _record(
        conn,
        tenant_id,
        name="agent.activated",
        event=True,
        aggregate_type="agent",
        aggregate_id=agent_id,
        actor=actor,
        data={"agent_principal_id": agent_id, "security_revision": new.security_revision},
    )
    return new


async def _suspend_one(
    conn: AsyncConnection,
    tenant_id: str,
    agent: AgentProfile,
    *,
    reason: str,
    actor: str | None,
    actor_type: str = "HUMAN",
) -> None:
    if agent.status is AgentStatus.ACTIVE:
        await machine_store.change_agent_status(
            conn, agent.agent_principal_id, AgentStatus.SUSPENDED
        )
        await _set_principal(conn, agent.agent_principal_id, PrincipalStatus.SUSPENDED)
    await _record(
        conn,
        tenant_id,
        name="agent.suspended",
        event=True,
        aggregate_type="agent",
        aggregate_id=agent.agent_principal_id,
        actor=actor,
        data={"agent_principal_id": agent.agent_principal_id, "reason_code": reason},
        actor_type=actor_type,
    )


async def suspend(
    conn: AsyncConnection,
    tenant_id: str,
    agent_id: str,
    *,
    reason: str,
    actor: str | None,
    actor_type: str = "HUMAN",
) -> dict[str, Any]:
    """The kill path (§12.1): the agent, every sub-agent below it, and their live bindings stop now."""
    agent = await _load(conn, agent_id)
    if agent.status is not AgentStatus.ACTIVE:
        raise MachineError("AGENT_INVALID_TRANSITION", f"{agent.status.value} -> SUSPENDED")
    below = await agent_store.descendants(conn, agent_id)
    await _suspend_one(conn, tenant_id, agent, reason=reason, actor=actor, actor_type=actor_type)
    for child_id in below:
        child = await _load(conn, child_id)
        if child.status is AgentStatus.ACTIVE:
            await _suspend_one(
                conn,
                tenant_id,
                child,
                reason="PARENT_SUSPENDED",
                actor=actor,
                actor_type=actor_type,
            )
    bindings = await agent_store.move_bindings(conn, agent_ids=[agent_id, *below], to="SUSPENDED")
    return {
        "agent_principal_id": agent_id,
        "sub_agents": len(below),
        "bindings_suspended": bindings,
    }


async def sponsor_is_active(conn: AsyncConnection, sponsor_id: str | None) -> bool:
    """Fails closed: a sponsor that cannot be read, or is not ACTIVE, is not accountable."""
    if not sponsor_id:
        return False
    sponsor = await _principal(conn, sponsor_id)
    return sponsor is not None and sponsor.status is PrincipalStatus.ACTIVE


async def lifecycle_sweep(conn: AsyncConnection, now: datetime) -> list[dict[str, Any]]:
    """Worker pass (BYPASSRLS), WP-10: suspend, by policy, every ACTIVE agent whose accountable sponsor has left
    (disabled, erased, or removed) or whose expiry has passed, together with its sub-agents and live bindings.
    Idempotent: a suspended agent is not picked again. Runs one tenant at a time under that tenant's scope."""
    rows = (
        await conn.execute(
            text(
                "SELECT a.tenant_id, a.agent_principal_id FROM agent.agent_profile a "
                "LEFT JOIN core.principal p ON p.principal_id = a.sponsor_principal_id "
                "WHERE a.status = 'ACTIVE' AND (a.sponsor_principal_id IS NULL "
                "OR p.principal_id IS NULL OR p.status <> 'ACTIVE' "
                "OR (a.expires_at IS NOT NULL AND a.expires_at <= :now)) "
                "ORDER BY a.tenant_id, a.created_at"
            ),
            {"now": now},
        )
    ).all()
    out: list[dict[str, Any]] = []
    for r in rows:
        tenant, agent_id = str(r.tenant_id), str(r.agent_principal_id)
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})
        agent = await machine_store.load_agent(conn, agent_id)
        if agent is None or agent.status is not AgentStatus.ACTIVE:
            continue  # a parent's suspension already took it
        reason = lifecycle_breach(
            agent, sponsor_active=await sponsor_is_active(conn, agent.sponsor_principal_id), now=now
        )
        if reason is None:
            continue
        result = await suspend(
            conn, tenant, agent_id, reason=reason, actor=None, actor_type="SYSTEM"
        )
        out.append({"tenant_id": tenant, "reason": reason, **result})
    return out


async def revoke(
    conn: AsyncConnection, tenant_id: str, agent_id: str, *, reason: str, actor: str
) -> dict[str, Any]:
    """Terminal. Sub-agents are suspended (not revoked) so the tree can be reviewed; bindings of the
    revoked agent are revoked, evidence is kept."""
    agent = await _load(conn, agent_id)
    below = await agent_store.descendants(conn, agent_id)
    await machine_store.change_agent_status(conn, agent_id, AgentStatus.REVOKED)
    await _set_principal(conn, agent_id, PrincipalStatus.DISABLED)
    for child_id in below:
        child = await _load(conn, child_id)
        if child.status is AgentStatus.ACTIVE:
            await _suspend_one(conn, tenant_id, child, reason="PARENT_REVOKED", actor=actor)
    revoked = await agent_store.move_bindings(
        conn, agent_ids=[agent_id], to="REVOKED", from_status=("ACTIVE", "SUSPENDED")
    )
    await agent_store.move_bindings(conn, agent_ids=below, to="SUSPENDED")
    await _record(
        conn,
        tenant_id,
        name="agent.revoked",
        event=True,
        aggregate_type="agent",
        aggregate_id=agent_id,
        actor=actor,
        data={"agent_principal_id": agent_id, "reason_code": reason, "was": agent.status.value},
    )
    return {
        "agent_principal_id": agent_id,
        "sub_agents_suspended": len(below),
        "bindings_revoked": revoked,
    }


async def retire(
    conn: AsyncConnection, tenant_id: str, agent_id: str, *, actor: str
) -> AgentProfile:
    new = await machine_store.change_agent_status(conn, agent_id, AgentStatus.RETIRED)
    await _set_principal(conn, agent_id, PrincipalStatus.DISABLED)
    await agent_store.move_bindings(
        conn, agent_ids=[agent_id], to="REVOKED", from_status=("ACTIVE", "SUSPENDED")
    )
    await _record(
        conn,
        tenant_id,
        name="agent_registry.retired",
        event=False,
        aggregate_type="agent",
        aggregate_id=agent_id,
        actor=actor,
        data={"agent_principal_id": agent_id},
        evidence=EvidenceClass.E2,
    )
    return new


# ---------------------------------------------------------------- tools (§14-15)


async def register_tool(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    tool_id: str,
    version: str,
    server_principal_id: str,
    canonical_action: str,
    default_risk_class: int,
    side_effect: str,
    requires_mandate: bool,
    requires_human_approval: bool,
    actor: str,
) -> Tool:
    if not ACTION_NAME.match(canonical_action):
        raise MachineError(
            "TOOL_ACTION_INVALID", "the canonical action must look like domain.resource.verb"
        )
    server = await _principal(conn, server_principal_id)
    if server is None or server.principal_type not in SERVER_TYPES:
        raise MachineError(
            "TOOL_SERVER_INVALID", "the hosting server must be a registered service or application"
        )
    if tool_id in await machine_store.load_tools(conn):
        raise MachineError("TOOL_EXISTS")
    tool = Tool(
        tool_id,
        version,
        server_principal_id,
        canonical_action,
        default_risk_class,
        SideEffect(side_effect),
        requires_mandate,
        requires_human_approval,
        ToolStatus.DRAFT,
    )
    # a side-effecting tool always needs a mandate, whatever the caller said (§15)
    stored = Tool(
        tool.tool_id,
        tool.version,
        tool.server_principal_id,
        tool.canonical_action,
        tool.default_risk_class,
        tool.side_effect,
        tool_requires_mandate(tool),
        tool.requires_human_approval,
        tool.status,
    )
    await machine_store.save_tool(conn, tenant_id, stored)
    await _record(
        conn,
        tenant_id,
        name="tool_registry.drafted",
        event=False,
        aggregate_type="tool",
        aggregate_id=tool_id,
        actor=actor,
        data={"tool_id": tool_id},
        evidence=EvidenceClass.E2,
    )
    return stored


async def change_tool(
    conn: AsyncConnection, tenant_id: str, tool_id: str, *, change: str, reason: str, actor: str
) -> dict[str, Any]:
    tools = await machine_store.load_tools(conn)
    tool = tools.get(tool_id)
    if tool is None:
        raise MachineError("TOOL_NOT_FOUND")
    allowed = {
        "activate": (ToolStatus.ACTIVE, {ToolStatus.DRAFT, ToolStatus.SUSPENDED}),
        "suspend": (ToolStatus.SUSPENDED, {ToolStatus.ACTIVE}),
        "retire": (ToolStatus.RETIRED, {ToolStatus.ACTIVE, ToolStatus.SUSPENDED, ToolStatus.DRAFT}),
    }[change]
    target, sources = allowed
    if tool.status not in sources:
        raise MachineError("TOOL_INVALID_TRANSITION", f"{tool.status.value} -> {target.value}")
    await agent_store.set_tool_status(conn, tool_id, target)
    event = {"activate": "tool.registered", "suspend": "tool.suspended"}.get(change)
    await _record(
        conn,
        tenant_id,
        name=event or f"tool_registry.{change}",
        event=event is not None,
        aggregate_type="tool",
        aggregate_id=tool_id,
        event_aggregate_id=f"{tenant_id}:{tool_id}",
        actor=actor,
        data={"tool_id": tool_id, "reason_code": reason},
    )
    refreshed = (await machine_store.load_tools(conn))[tool_id]
    return {
        **tool_view(refreshed),
        "agents_using": await agent_store.agents_with_tool(conn, tool_id),
    }
