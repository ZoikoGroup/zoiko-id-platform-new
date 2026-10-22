"""Gate evaluation for the tenant's current onboarding stage (OVERLAY-001 §25, §29; G-W1..W4).

Each stage contributes the gates that guard its exit. Evaluation only reads: the move itself is
made by the use case for that stage, which re-evaluates its gates inside its own transaction.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.onboarding.domain.gates import Gate, GateStatus, OnboardingError
from zid_platform.modules.organization.ports import has_usable_root
from zid_platform.modules.tenant.ports import (
    TenantStatus,
    active_owner_count,
    load_residency,
    load_tenant,
    verified_domain_count,
)

S = TenantStatus
StageGates = Callable[[AsyncConnection, str], Awaitable[list[Gate]]]


async def organization_gates(conn: AsyncConnection, tenant_id: str) -> list[Gate]:
    t = await load_tenant(conn, tenant_id)
    assert t is not None
    root_ok = bool(t.organization_id) and await has_usable_root(
        conn, tenant_id, t.organization_id or "", datetime.now(UTC)
    )
    return [
        Gate(
            "organization.root",
            S.ORGANIZATION_VERIFYING.value,
            GateStatus.PASS if root_ok else GateStatus.PENDING,
            None if root_ok else "TENANT_ORGANIZATION_UNVERIFIED",
        )
    ]


async def configuring_gates(conn: AsyncConnection, tenant_id: str) -> list[Gate]:
    t = await load_tenant(conn, tenant_id)
    assert t is not None
    stage = S.CONFIGURING.value
    residency = await load_residency(conn, tenant_id)
    placed = residency is not None and residency.home_cell_id == t.home_cell_id
    owners = await active_owner_count(conn)
    domains = await verified_domain_count(conn)
    return [
        Gate(
            "tenant.owner",
            stage,
            GateStatus.PASS if owners else GateStatus.FAIL,
            None if owners else "TENANT_OWNER_UNAUTHORIZED",
        ),
        Gate(
            "residency.placement",
            stage,
            GateStatus.PASS if placed else GateStatus.PENDING,
            None if placed else "RESIDENCY_PLACEMENT_REQUIRED",
            "" if placed else "Set the tenant's residency profile and home cell first.",
        ),
        Gate(
            "tenant.domain",
            stage,
            GateStatus.PASS if domains else GateStatus.PENDING,
            None if domains else "DOMAIN_VERIFICATION_FAILED",
            "" if domains else "Verify at least one enterprise domain (DNS TXT or HTTPS).",
        ),
    ]


STAGES: dict[TenantStatus, StageGates] = {
    S.ORGANIZATION_VERIFYING: organization_gates,
    S.CONFIGURING: configuring_gates,
}


async def evaluate(conn: AsyncConnection, tenant_id: str) -> tuple[str, list[Gate]]:
    t = await load_tenant(conn, tenant_id)
    if t is None:
        raise OnboardingError("TENANT_NOT_FOUND")
    fn = STAGES.get(t.status)
    return t.status.value, (await fn(conn, tenant_id) if fn else [])
