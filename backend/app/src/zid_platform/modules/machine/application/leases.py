"""Short-lived, audience-restricted, purpose-bound workload leases for an in-process agent run (CR-091; ZID-AG-WF-001 section 09 step 3).

A first-party agent that runs inside the platform (the access readiness agent) has no mesh connection of its own, so it cannot present
an SVID to `POST /v1/machine/workload-token`. It still gets its credential from the same issuance decision: the agent's live workload
binding is checked by `machine_store.credential_check` (agent ACTIVE, binding ACTIVE and in date, the approved artifact, no revoked
blueprint, no quarantined environment, an attestation level that meets the binding and the risk floor), its verified attestation level
is read from the attestation records, never claimed, and the issuance is recorded as the same `machine.workload.credential_issued`
evidence. What is handed back is a lease: an opaque id, the audience and purpose it is bound to and an expiry. It is not a bearer
token, so there is nothing secret to store or log.

`lease_problems` re-checks a lease at every step it is used for (expiry, audience, purpose, a revoked or rebound binding, a changed
security revision, a quarantine), so a revocation takes effect at the next step, and `revoke_workload_lease` ends it when the run ends.
An unattested or revoked worker gets no lease (fail closed).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7

from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit
from zid_platform.modules.machine.application.api_proof import api_proof
from zid_platform.modules.machine.application.workload import (
    WorkloadPrincipal,
    record_credential_issued,
)
from zid_platform.modules.machine.domain.agents import Attestation, CredentialRequest, MachineError
from zid_platform.modules.machine.infrastructure import machine_store
from zid_platform.modules.machine.infrastructure import workload_store as store

LEASE_LIFETIME = timedelta(minutes=5)  # the workload token's own lifetime
LEASE_RISK_LEVEL = 2  # the floor the workload-token path applies
LEASE_REVOKED = "machine.workload.lease_revoked"  # audit-only (a record in the audit trail, not an event on the bus)


@dataclass(slots=True)
class WorkloadLease:
    lease_id: str
    agent_principal_id: str
    binding_id: str
    environment_id: str
    security_revision: int
    audience: str
    purpose: str
    scope: frozenset[str]
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    workload_identity_id: str = ""
    artifact_digest: str = ""
    # WORKLOAD: backed by a live workload binding and a verified attestation. API_CLIENT: backed by a bound API client's key, at the level a key can
    # show (CR-142). It is the same in-process lease either way: an opaque id, never a bearer token.
    kind: str = "WORKLOAD"


async def _bindings(conn: AsyncConnection, agent_id: str) -> list[Any]:
    import uuid

    rows = await conn.execute(
        text(
            "SELECT * FROM agent.workload_binding WHERE logical_principal_id = :a AND status = 'ACTIVE' "
            "ORDER BY valid_to DESC, binding_id"
        ),
        {"a": uuid.UUID(agent_id)},
    )
    return list(rows.all())


async def _check(
    conn: AsyncConnection,
    workload_identity_id: str,
    environment_id: str,
    artifact: str,
    now: datetime,
) -> list[str]:
    """The workload-token issuance decision, from stored state only. Unattested is a refusal whatever the floor says."""
    level = await store.verified_level(conn, workload_identity_id, environment_id, artifact, now)
    req = CredentialRequest(
        workload_identity_id,
        artifact,
        Attestation(min(level, int(Attestation.ATTEST_3))),
        environment_id,
        now,
        LEASE_RISK_LEVEL,
    )
    problems = await machine_store.credential_check(conn, req)
    if level < int(Attestation.ATTEST_1) and "ATTESTATION_INSUFFICIENT" not in problems:
        problems.append("ATTESTATION_INSUFFICIENT")
    return problems


async def issue_workload_lease(
    conn: AsyncConnection,
    tenant_id: str,
    agent_id: str,
    *,
    audience: str,
    purpose: str,
    scope: frozenset[str],
    now: datetime | None = None,
    lifetime: timedelta = LEASE_LIFETIME,
) -> WorkloadLease:
    """A lease for the agent's best live binding, or MachineError naming why none can be issued."""
    if not audience or not purpose or not scope:
        raise MachineError(
            "CREDENTIAL_SCOPE_REQUIRED", "a lease names its audience, purpose and scope"
        )
    now = now or datetime.now(UTC)
    rows = await _bindings(conn, agent_id)
    if not rows:
        # No container binding at all: an agent bound to an API client holds its proof as a key, not an image (CR-142).
        return await _api_lease(
            conn,
            tenant_id,
            agent_id,
            audience=audience,
            purpose=purpose,
            scope=scope,
            now=now,
            lifetime=lifetime,
        )
    problems: list[str] = []
    for row in rows:
        found = await _check(
            conn, row.workload_identity_id, row.environment_id, row.artifact_digest, now
        )
        if found:
            problems.extend(p for p in found if p not in problems)
            continue
        expires = now + min(lifetime, LEASE_LIFETIME)
        principal = WorkloadPrincipal(
            row.workload_identity_id,
            agent_id,
            str(row.binding_id),
            row.environment_id,
            int(row.security_revision),
        )
        await record_credential_issued(conn, tenant_id, principal, expires_at=expires)
        return WorkloadLease(
            lease_id=str(uuid7()),
            agent_principal_id=agent_id,
            binding_id=str(row.binding_id),
            environment_id=row.environment_id,
            security_revision=int(row.security_revision),
            audience=audience,
            purpose=purpose,
            scope=frozenset(scope),
            issued_at=now,
            expires_at=expires,
            workload_identity_id=row.workload_identity_id,
            artifact_digest=row.artifact_digest,
        )
    raise MachineError(problems[0], ", ".join(problems))


async def _api_lease(
    conn: AsyncConnection,
    tenant_id: str,
    agent_id: str,
    *,
    audience: str,
    purpose: str,
    scope: frozenset[str],
    now: datetime,
    lifetime: timedelta,
) -> WorkloadLease:
    """A lease backed by the agent's bound API client, issued only when the token path would issue the agent a credential too (one decision, in
    `credential_problems`). It carries the identity `api-client:<client id>`, never a key or a token, and lasts no longer than any other lease."""
    proof, problems = await api_proof(conn, agent_id, now)
    if proof is None:
        raise MachineError(
            "WORKLOAD_IDENTITY_UNREGISTERED",
            "the agent has no live workload binding and no bound API client",
        )
    if problems:
        raise MachineError(problems[0], ", ".join(problems))
    identity = f"api-client:{proof.client_id}"
    expires = now + min(lifetime, LEASE_LIFETIME)
    await record_credential_issued(
        conn,
        tenant_id,
        WorkloadPrincipal(identity, agent_id, proof.binding_id, "api", proof.security_revision),
        expires_at=expires,
    )
    return WorkloadLease(
        lease_id=str(uuid7()),
        agent_principal_id=agent_id,
        binding_id=proof.binding_id,
        environment_id="api",
        security_revision=proof.security_revision,
        audience=audience,
        purpose=purpose,
        scope=frozenset(scope),
        issued_at=now,
        expires_at=expires,
        workload_identity_id=identity,
        artifact_digest=proof.descriptor_digest,
        kind="API_CLIENT",
    )


async def lease_problems(
    conn: AsyncConnection,
    lease: WorkloadLease,
    *,
    audience: str,
    purpose: str,
    use: str,
    now: datetime | None = None,
) -> list[str]:
    """Why this lease may not be used for this step now; empty means it may. Never a broader credential: a problem is a stop."""
    now = now or datetime.now(UTC)
    if lease.revoked_at is not None:
        return ["CREDENTIAL_REVOKED"]
    out: list[str] = []
    if now >= lease.expires_at:
        out.append("CREDENTIAL_EXPIRED")
    if audience != lease.audience:
        out.append("CREDENTIAL_AUDIENCE")
    if purpose != lease.purpose:
        out.append("CREDENTIAL_PURPOSE")
    if use not in lease.scope:
        out.append("CREDENTIAL_SCOPE")
    if lease.kind == "API_CLIENT":
        proof, problems = await api_proof(conn, lease.agent_principal_id, now)
        if (
            proof is None
            or proof.binding_id != lease.binding_id
            or proof.security_revision != lease.security_revision
        ):
            out.append("CREDENTIAL_REVOKED")  # revoked, rebound or changed since it was issued
        else:
            out.extend(problems)
        return list(dict.fromkeys(out))
    binding = await store.active_binding(conn, lease.workload_identity_id)
    if (
        binding is None
        or str(binding.binding_id) != lease.binding_id
        or int(binding.security_revision) != lease.security_revision
    ):
        out.append("CREDENTIAL_REVOKED")
    else:
        out.extend(
            await _check(
                conn, lease.workload_identity_id, lease.environment_id, lease.artifact_digest, now
            )
        )
    return list(dict.fromkeys(out))


async def revoke_workload_lease(
    conn: AsyncConnection,
    tenant_id: str,
    lease: WorkloadLease,
    *,
    reason: str,
    now: datetime | None = None,
) -> bool:
    """End the lease (the run is over or was stopped). Recorded once; False when it was already revoked."""
    if lease.revoked_at is not None:
        return False
    lease.revoked_at = now or datetime.now(UTC)
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type=LEASE_REVOKED,
            actor_type="SYSTEM",
            action="lease_revoked",
            target_type="workload_binding",
            target_ref=lease.binding_id,
            outcome="SUCCESS",
            reason_code=reason,
            evidence_class=EvidenceClass.E3,
            occurred_at=lease.revoked_at,
            actor_principal_id=None,
            source_service="machine",
            source_event_id=None,
            details={
                "lease_id": lease.lease_id,
                "agent_principal_id": lease.agent_principal_id,
                "audience": lease.audience,
                "purpose": lease.purpose,
                "expires_at": lease.expires_at.isoformat(),
            },
        ),
    )
    return True
