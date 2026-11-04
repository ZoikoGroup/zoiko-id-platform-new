"""Progressive verification (ZID-EDR-006; slice L-6).

GET /v1/verification/tier          where the caller is (R0 to R3), what the next tier needs, their claims with method, source, policy, scope, expiry and revocation
GET /v1/verification/requirements  the checks to reach a tier, with the reason each optional check is or is not required

Reading one's own standing is not a tenant administration act, so it needs no matrix action (route-map.yaml: OWN_ACCOUNT)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verification.application import tiers as service
from zid_platform.modules.verification.domain import tiers as t
from zid_platform.modules.verification.infrastructure.tier_policy import tier_policy

router = APIRouter(tags=["verification"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


@router.get("/v1/verification/tier")
async def my_tier(
    request: Request,
    caller: CallerDep,
    cannot: Annotated[list[str] | None, Query(max_length=6)] = None,
) -> dict[str, Any]:
    """`cannot` names what the person cannot do (NO_CAMERA, NO_PHONE, NO_STANDARD_ID) so the approved alternatives are shown; nobody else is steered away."""
    principal, tenant = caller.ctx.principal_id, caller.ctx.tenant_id
    if not principal or not tenant:
        raise ZidError("UNAUTHENTICATED", "An authenticated person is required.", http_status=401)
    # the organization reader is handed in at start-up: verification cannot import onboarding (the modules would form a cycle)
    org_state = getattr(request.app.state, "organization_state", None)

    now = datetime.now(UTC)
    policy = tier_policy()
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        facts = await service.facts_for(
            conn, principal_id=principal, authn_level=int(caller.authn.level),
            registration_tenant=getattr(request.app.state, "registration_tenant", None),
            organization_state=await org_state(conn, tenant, now) if org_state else None, now=now,
        )  # fmt: skip
        return await service.tier_view(
            conn, policy, facts, principal_id=principal, now=now, needs=set(cannot or [])
        )


@router.get("/v1/verification/requirements")
async def requirements(
    request: Request,
    caller: CallerDep,
    target: Annotated[str, Query(pattern="^R[0-3]$")],
    need: Annotated[list[str] | None, Query(max_length=5)] = None,
    alternative: Annotated[str | None, Query(max_length=40)] = None,
) -> dict[str, Any]:
    """The checks to reach `target`. An optional check is `required` only for one of its stated reasons; naming a reason that is not one is refused."""
    policy = tier_policy()
    try:
        needs = frozenset(t.Need(n.upper()) for n in need or [])
        checks = t.required_checks(t.Tier(target), policy, needs=needs, alternative=alternative)
    except (ValueError, t.TierError) as e:
        raise ZidError(
            "REQUIREMENT_INVALID",
            "That is not a recognised target, reason or alternative.",
            http_status=422,
        ) from e
    return {
        "policy_version": policy.version,
        "target": target,
        "checks": [{"check": c.check, "required": c.required, "reason": c.reason} for c in checks],
    }
