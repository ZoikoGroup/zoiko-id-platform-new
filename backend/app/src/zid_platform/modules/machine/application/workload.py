"""Workload identity: SPIFFE ID -> registered binding -> logical principal (DEC-F7 §3-4; AI Agent
spec §5, §16; CRYPTO-MI-001 §25-29; F-W6).

    SVID (mesh-verified) -> WorkloadBinding -> logical principal (SERVICE / WORKLOAD / AI_AGENT)
                         + environment + artifact evidence + current security_revision
                         -> then authority + mandate + policy evaluation, never before

- A SPIFFE ID authenticates; the binding decides which logical principal it may act as. An
  unregistered ID maps to nothing; a workload can never register or rebind itself.
- Quarantine (environment) and revocation (binding) bump `security_revision` and refuse the very
  next resolution - the R3 revocation path, independent of certificate lifetimes.
- Every binding and quarantine change is an E3 protected AuditRecord.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_context.spiffe import SpiffeError, SpiffeId, TrustPolicy, tenant_of
from zid_outbox import emit

from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit
from zid_platform.modules.machine.application.agents import normalize_digest, require_build_approved
from zid_platform.modules.machine.application.attestation import Statement
from zid_platform.modules.machine.domain.agents import (
    Attestation,
    BindingStatus,
    CredentialRequest,
    MachineError,
    ReviewStatus,
    WorkloadBinding,
)
from zid_platform.modules.machine.infrastructure import machine_store
from zid_platform.modules.machine.infrastructure import workload_store as store


@dataclass(frozen=True, slots=True)
class WorkloadPrincipal:
    spiffe_id: str
    logical_principal_id: str
    binding_id: str
    environment_id: str
    security_revision: int


def _now() -> datetime:
    return datetime.now(UTC)


async def _audit(
    conn: AsyncConnection,
    tenant_id: str,
    event_id: str | None,
    name: str,
    *,
    actor: str | None,
    target_type: str,
    target_ref: str,
    outcome: str = "SUCCESS",
    reason: str | None = None,
    details: dict[str, object] | None = None,
) -> None:
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type=name,
            actor_type="HUMAN" if actor else "SYSTEM",
            action=name.rsplit(".", 1)[-1],
            target_type=target_type,
            target_ref=target_ref,
            outcome=outcome,
            reason_code=reason,
            evidence_class=EvidenceClass.E3,
            occurred_at=_now(),
            actor_principal_id=actor,
            source_service="machine",
            source_event_id=event_id,
            details=dict(details or {}),
        ),
    )


async def register_binding(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    policy: TrustPolicy,
    agent_principal_id: str,
    spiffe_id: str,
    blueprint_version: str,
    artifact_digest: str,
    environment_id: str,
    attestation_required: int,
    valid_to: datetime,
    actor: str,
    require_approved_build: bool = False,
) -> WorkloadBinding:
    """Governed registration (machine.workload.manage, R3): the trust domain must be this cell's or
    an explicitly federated one; the agent must exist; one live binding per SPIFFE ID."""
    try:
        sid = policy.require(SpiffeId.parse(spiffe_id))
        if tenant_of(sid) != tenant_id:
            raise SpiffeError("SPIFFE_ID_INVALID", "the SPIFFE ID belongs to another tenant")
    except SpiffeError as e:
        raise MachineError(e.code, str(e)) from e
    agent = await machine_store.load_agent(conn, agent_principal_id)
    if agent is None:
        raise MachineError("AGENT_NOT_FOUND")
    if not artifact_digest.startswith(("sha256:", "sha-256:")):
        raise MachineError("WORKLOAD_ARTIFACT_NOT_APPROVED")
    # A workload runs the software of the release its agent is built from, and nothing else (CR-064): the digest and version must be
    # that release's, the release must be approved, and where approved builds are required its build must be too. Without this a
    # binding could name any digest, and the console filling in the right one was the only thing keeping it honest.
    release = await machine_store.load_blueprint(conn, agent.blueprint_id, agent.blueprint_version)
    if release is None or release.review is not ReviewStatus.APPROVED:
        raise MachineError("AGENT_BLUEPRINT_NOT_APPROVED", "the agent's blueprint release is not approved")
    if blueprint_version != agent.blueprint_version or normalize_digest(artifact_digest) != normalize_digest(release.artifact_digest):
        raise MachineError(
            "WORKLOAD_ARTIFACT_NOT_APPROVED",
            "a binding must run the artifact of its agent's approved blueprint release",
        )
    if require_approved_build:
        await require_build_approved(conn, artifact_digest)
    if await store.active_binding(conn, str(sid)) is not None:
        raise MachineError("WORKLOAD_IDENTITY_ALREADY_BOUND")
    now = _now()
    if valid_to <= now:
        raise MachineError("WORKLOAD_BINDING_INACTIVE")
    binding = WorkloadBinding(
        str(uuid7()),
        agent_principal_id,
        str(sid),
        blueprint_version,
        artifact_digest,
        environment_id,
        Attestation(attestation_required),
        BindingStatus.ACTIVE,
        now,
        valid_to,
    )
    await machine_store.save_binding(conn, tenant_id, binding)
    data = {
        "binding_id": binding.binding_id,
        "spiffe_id": str(sid),
        "logical_principal_id": agent_principal_id,
        "environment_id": environment_id,
        "artifact_digest": artifact_digest,
        "attestation_required": attestation_required,
    }
    event_id = await emit(
        conn,
        name="machine.workload.registered",
        tenant_id=tenant_id,
        aggregate_type="workload_binding",
        aggregate_id=binding.binding_id,
        aggregate_version=1,
        data={**data, "evidence_class": "E3"},
    )
    await _audit(
        conn,
        tenant_id,
        event_id,
        "machine.workload.registered",
        actor=actor,
        target_type="workload_binding",
        target_ref=binding.binding_id,
        details={k: v for k, v in data.items() if k != "logical_principal_id"},
    )
    return binding


async def revoke_binding(
    conn: AsyncConnection, tenant_id: str, spiffe_id: str, *, reason: str, actor: str | None
) -> int:
    changed = await machine_store.set_binding_status(conn, spiffe_id, BindingStatus.REVOKED)
    if not changed:
        raise MachineError("WORKLOAD_IDENTITY_UNREGISTERED")
    event_id = await emit(
        conn,
        name="machine.workload.binding_revoked",
        tenant_id=tenant_id,
        aggregate_type="workload_identity",
        aggregate_id=spiffe_id,
        aggregate_version=1,
        data={"spiffe_id": spiffe_id, "reason_code": reason, "evidence_class": "E3"},
    )
    await _audit(
        conn,
        tenant_id,
        event_id,
        "machine.workload.binding_revoked",
        actor=actor,
        target_type="workload_identity",
        target_ref=spiffe_id,
        reason=reason,
    )
    return changed


async def quarantine_environment(
    conn: AsyncConnection, tenant_id: str, environment_id: str, *, reason: str, actor: str | None
) -> int:
    """Every workload of the environment is refused from the next request on (MACH-10/21)."""
    rev = await store.quarantine(conn, tenant_id, environment_id, reason, actor)
    if rev is None:
        raise MachineError("ENVIRONMENT_ALREADY_QUARANTINED")
    for name in ("machine.environment.quarantined", "machine.security_revision.changed"):
        event_id = await emit(
            conn,
            name=name,
            tenant_id=tenant_id,
            aggregate_type="workload_environment",
            aggregate_id=f"{tenant_id}:{environment_id}:{name.rsplit('.', 1)[-1]}",
            aggregate_version=rev,
            data={
                "environment_id": environment_id,
                "security_revision": rev,
                "reason_code": reason,
                "evidence_class": "E3",
            },
        )
        await _audit(
            conn,
            tenant_id,
            event_id,
            name,
            actor=actor,
            target_type="workload_environment",
            target_ref=environment_id,
            reason=reason,
            details={"security_revision": rev},
        )
    return rev


async def release_environment(
    conn: AsyncConnection, tenant_id: str, environment_id: str, *, actor: str
) -> None:
    if not await store.lift(conn, environment_id, actor):
        raise MachineError("ENVIRONMENT_NOT_QUARANTINED")
    event_id = await emit(
        conn,
        name="machine.environment.released",
        tenant_id=tenant_id,
        aggregate_type="workload_environment_release",
        aggregate_id=str(uuid7()),
        aggregate_version=1,
        data={"environment_id": environment_id, "evidence_class": "E3"},
    )
    await _audit(
        conn,
        tenant_id,
        event_id,
        "machine.environment.released",
        actor=actor,
        target_type="workload_environment",
        target_ref=environment_id,
    )


async def resolve_workload(
    conn: AsyncConnection,
    *,
    spiffe_id: SpiffeId,
    environment_id: str,
    artifact_digest: str,
    attestation: int | None = None,
    risk_level: int = 2,
) -> WorkloadPrincipal:
    """The logical principal a mesh-verified workload may act as right now, or MachineError with
    every blocker (unregistered, inactive, wrong artifact, revoked blueprint, quarantined, weak
    attestation). `spiffe_id` must come from `zid_context.spiffe.peer_spiffe_id`, never a body.
    `attestation` None (the API path) reads the level from verified attestation records (CR-042); a level passed
    here is for callers that already hold a verified one."""
    level = (
        attestation
        if attestation is not None
        else await store.verified_level(
            conn, str(spiffe_id), environment_id, artifact_digest, _now()
        )
    )
    req = CredentialRequest(
        str(spiffe_id),
        artifact_digest,
        Attestation(level),
        environment_id,
        _now(),
        risk_level,
    )
    blockers = await machine_store.credential_check(
        conn, req, quarantined_environments=await store.quarantined_environments(conn)
    )
    if blockers:
        raise MachineError(blockers[0], ", ".join(blockers))
    row = await store.active_binding(conn, str(spiffe_id))
    assert row is not None
    return WorkloadPrincipal(
        str(spiffe_id),
        str(row.logical_principal_id),
        str(row.binding_id),
        row.environment_id,
        int(row.security_revision),
    )


async def record_attestation(
    conn: AsyncConnection, stmt: Statement, *, failure_code: str | None = None
) -> str:
    """Keep one verified (or, for a signed statement that is out of date, failed) attestation as evidence, emit its
    event and audit it. Returns the attestation id."""
    attestation_id = str(uuid7())
    await store.add_attestation(
        conn,
        attestation_id=attestation_id,
        tenant_id=stmt.tenant_id,
        workload_identity_id=stmt.spiffe_id,
        environment_id=stmt.environment_id,
        artifact_digest=stmt.artifact_digest,
        level=stmt.level,
        verifier=stmt.verifier,
        claims_commitment=stmt.commitment,
        status="FAILED" if failure_code else "VALID",
        failure_code=failure_code,
        valid_until=stmt.valid_until,
    )
    name = "workload.attestation.failed" if failure_code else "workload.attestation.verified"
    data = {
        "attestation_id": attestation_id,
        "spiffe_id": stmt.spiffe_id,
        "environment_id": stmt.environment_id,
        "level": stmt.level,
        "verifier": stmt.verifier,
        "valid_until": stmt.valid_until.isoformat(),
        "claims_commitment": stmt.commitment,
        **({"failure_code": failure_code} if failure_code else {}),
    }
    event_id = await emit(
        conn,
        name=name,
        tenant_id=stmt.tenant_id,
        aggregate_type="workload_attestation",
        aggregate_id=attestation_id,
        aggregate_version=1,
        data={**data, "evidence_class": "E2"},
    )
    await _audit(
        conn,
        stmt.tenant_id,
        event_id,
        name,
        actor=None,
        target_type="workload_attestation",
        target_ref=attestation_id,
        outcome="DENIED" if failure_code else "SUCCESS",
        reason=failure_code,
        details=data,
    )
    return attestation_id


__all__ = [
    "WorkloadPrincipal",
    "quarantine_environment",
    "record_attestation",
    "register_binding",
    "release_environment",
    "resolve_workload",
    "revoke_binding",
]


async def record_credential_issued(
    conn: AsyncConnection, tenant_id: str, principal: WorkloadPrincipal, *, expires_at: datetime
) -> None:
    """E3 evidence that a workload credential was issued for a logical principal (§16)."""
    data = {
        "spiffe_id": principal.spiffe_id,
        "binding_id": principal.binding_id,
        "environment_id": principal.environment_id,
        "security_revision": principal.security_revision,
        "expires_at": expires_at.isoformat(),
    }
    event_id = await emit(
        conn,
        name="machine.workload.credential_issued",
        tenant_id=tenant_id,
        aggregate_type="workload_credential",
        aggregate_id=str(uuid7()),
        aggregate_version=1,
        data={**data, "evidence_class": "E3"},
    )
    await _audit(
        conn,
        tenant_id,
        event_id,
        "machine.workload.credential_issued",
        actor=None,
        target_type="workload_binding",
        target_ref=principal.binding_id,
        details=data,
    )
