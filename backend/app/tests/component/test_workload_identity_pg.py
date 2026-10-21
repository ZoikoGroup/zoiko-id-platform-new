"""Workload identity: SPIFFE ID -> binding -> logical principal, quarantine and revocation against
PostgreSQL with RLS (F-W6; DEC-F7 acceptance; MACH-03/10/21)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from zid_context.spiffe import SpiffeId, TrustPolicy
from zid_db import tenant_transaction
from zid_platform.modules.evidence.application.runtime import configure_runtime, development_runtime
from zid_platform.modules.machine.application import workload
from zid_platform.modules.machine.domain.agents import (
    ActivationEvidence,
    AgentProfile,
    AgentStatus,
    BlueprintRelease,
    MachineError,
    ReviewStatus,
)
from zid_platform.modules.machine.infrastructure import machine_store

DIGEST = "sha256:" + "ab" * 32
POLICY = TrustPolicy("uk-1.test.id.zoiko", federated=frozenset({"partner.test.id.zoiko"}))
SID = "spiffe://uk-1.test.id.zoiko/ns/payments/sa/agent"


def uid() -> str:
    return str(uuid.uuid4())


def test_workload_resolution_quarantine_and_revocation(rt_url: str) -> None:
    configure_runtime(development_runtime("uk-1"))
    tenant, agent_id, admin = uid(), uid(), uid()
    bp = BlueprintRelease("bp_pay", "1.0.0", DIGEST, "sha256:tools", uid(), ReviewStatus.APPROVED)

    async def run() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await machine_store.save_blueprint(c, tenant, bp)
            await machine_store.save_agent(
                c,
                AgentProfile(agent_id, tenant, "Payments agent", "bp_pay", "1.0.0", bp.publisher_principal_id,
                             uid(), uid(), uid(), "any", frozenset({"tool_pay"}), 1, AgentStatus.PENDING_APPROVAL),
            )
            await machine_store.change_agent_status(
                c, agent_id, AgentStatus.ACTIVE,
                evidence=ActivationEvidence(True, None, True, True, True, True, True),
            )
            with pytest.raises(MachineError, match="SPIFFE_TRUST_DOMAIN_UNTRUSTED"):
                await workload.register_binding(
                    c, tenant, policy=POLICY, agent_principal_id=agent_id,
                    spiffe_id="spiffe://eu-1.test.id.zoiko/ns/payments/sa/agent", blueprint_version="1.0.0",
                    artifact_digest=DIGEST, environment_id="prod", attestation_required=0,
                    valid_to=datetime.now(UTC) + timedelta(days=1), actor=admin,
                )
            b = await workload.register_binding(
                c, tenant, policy=POLICY, agent_principal_id=agent_id, spiffe_id=SID,
                blueprint_version="1.0.0", artifact_digest=DIGEST, environment_id="prod",
                attestation_required=0, valid_to=datetime.now(UTC) + timedelta(days=1), actor=admin,
            )
            with pytest.raises(MachineError, match="ALREADY_BOUND"):  # one live binding per SPIFFE ID
                await workload.register_binding(
                    c, tenant, policy=POLICY, agent_principal_id=agent_id, spiffe_id=SID,
                    blueprint_version="1.0.0", artifact_digest=DIGEST, environment_id="prod",
                    attestation_required=0, valid_to=datetime.now(UTC) + timedelta(days=1), actor=admin,
                )
            p = await workload.resolve_workload(
                c, spiffe_id=SpiffeId.parse(SID), environment_id="prod", artifact_digest=DIGEST, attestation=2
            )
            assert p.logical_principal_id == agent_id and p.binding_id == b.binding_id
            with pytest.raises(MachineError, match="WORKLOAD_IDENTITY_UNREGISTERED"):
                await workload.resolve_workload(
                    c, spiffe_id=SpiffeId.parse(SID + "-2"), environment_id="prod", artifact_digest=DIGEST, attestation=2
                )
            with pytest.raises(MachineError, match="WORKLOAD_ARTIFACT_NOT_APPROVED"):
                await workload.resolve_workload(
                    c, spiffe_id=SpiffeId.parse(SID), environment_id="prod", artifact_digest="sha256:" + "00" * 32, attestation=2
                )
            rev = await workload.quarantine_environment(c, tenant, "prod", reason="COMPROMISE_SUSPECTED", actor=admin)
            assert rev > p.security_revision
            with pytest.raises(MachineError, match="ENVIRONMENT_QUARANTINED"):  # denied at once
                await workload.resolve_workload(
                    c, spiffe_id=SpiffeId.parse(SID), environment_id="prod", artifact_digest=DIGEST, attestation=2
                )
            await workload.release_environment(c, tenant, "prod", actor=admin)
            p2 = await workload.resolve_workload(
                c, spiffe_id=SpiffeId.parse(SID), environment_id="prod", artifact_digest=DIGEST, attestation=2
            )
            assert p2.security_revision > p.security_revision  # caches keyed on it are invalid
            await workload.revoke_binding(c, tenant, SID, reason="ROTATED_OUT", actor=admin)
            with pytest.raises(MachineError, match="WORKLOAD_IDENTITY_UNREGISTERED"):
                await workload.resolve_workload(
                    c, spiffe_id=SpiffeId.parse(SID), environment_id="prod", artifact_digest=DIGEST, attestation=2
                )
            audits = set((await c.execute(text("SELECT event_type FROM evidence.audit_record"))).scalars())
            assert {"machine.workload.registered", "machine.environment.quarantined",
                    "machine.workload.binding_revoked"} <= audits

    asyncio.run(run())
    configure_runtime(None)
