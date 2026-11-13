"""Provider entitlements at runtime (ZID-INT-002 section 08; event `provider.entitlement_revoked`).

    POST /v2/providers/{provider_id}/entitlements                      record what a provider may be used for (one live record per service)
    GET  /v2/providers/entitlements                                    list them
    POST /v2/providers/{provider_id}/entitlements/{service}/suspend    stop using it for now (the record stays; a new record needs this one revoked first)
    POST /v2/providers/{provider_id}/entitlements/{service}/revoke     stop using it for good; announced as `provider.entitlement_revoked`
    GET  /v2/providers/{provider_id}/entitlements/{service}/impact     what suspending or revoking it would do, scope by scope (read-only)
    POST /v2/providers/route                                           which provider would be used, and why every other one would not (a dry run)

The rules are the ones in `verification/domain/provider_adapter.py`: legality (service, country, purpose, region, period, status) outranks
assurance, speed and cost, and an expired, suspended or revoked entitlement disables the path with no one having to remember. Revoked is
final (a trigger refuses to reopen it); a new period is a new record. This registers SCOPE ONLY: no contract, credential or adapter, and
recording an entitlement does not make a provider usable, since no real provider adapter is connected in this deployment.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_context import current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verification.application.entitlements import (
    EntitlementNotFound,
    change_status,
)
from zid_platform.modules.verification.domain import provider_adapter as pa
from zid_platform.modules.verification.infrastructure import entitlement_store as store
from zid_platform.protected import protected_action

router = APIRouter(tags=["verification-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
Slug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_.-]{1,62}$")]
Country = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
LIMITATIONS = [
    "This registers what a provider may be used for. It is not a contract and holds no credential; no real provider adapter is connected here.",
    "Revoked is final. A new period is a new record.",
    "An entitlement that has expired, is suspended or is revoked disables the path automatically.",
]


class EntitlementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_code: Slug
    countries: Annotated[list[Country], Field(min_length=1, max_length=250)]
    purposes: Annotated[list[Slug], Field(min_length=1, max_length=40)]
    processing_regions: Annotated[list[Slug], Field(min_length=1, max_length=20)]
    valid_from: date
    valid_until: date | None = None
    assurance: Annotated[int, Field(ge=0, le=10)]
    cost_rank: Annotated[int, Field(ge=0, le=1000)] = 0


class ReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Annotated[str, Field(min_length=3, max_length=200)]


class RouteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    country: Country
    service_code: Slug
    purpose: Slug
    processing_region: Slug
    required_assurance: Annotated[int, Field(ge=0, le=10)] = 0
    failing_over_from: Slug | None = None
    downgrade_approval_ref: Annotated[str, Field(max_length=120)] | None = None


def _ids() -> dict[str, str]:
    ctx = current_context()
    return {
        "request_id": ctx.request_id if ctx else "req_unknown",
        "correlation_id": ctx.correlation_id if ctx else "cor_unknown",
    }


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "entitlement_id": str(row["entitlement_id"]),
        "provider_id": row["provider_id"],
        "service_code": row["service_code"],
        "countries": sorted(row["countries"]),
        "purposes": sorted(row["purposes"]),
        "processing_regions": sorted(row["processing_regions"]),
        "valid_from": row["valid_from"].isoformat(),
        "valid_until": row["valid_until"].isoformat() if row["valid_until"] else None,
        "assurance": row["assurance"],
        "status": row["status"],
        "status_reason": row["status_reason"],
        "status_changed_at": row["status_changed_at"].isoformat(),
    }


async def _guard(conn: Any, caller: Caller, resource: str) -> str:
    await protected_action(conn, caller, action="credential.claim.verify", resource=resource)
    return str(caller.ctx.tenant_id)


@router.post("/v2/providers/{provider_id}/entitlements", status_code=201)
async def record_entitlement(
    provider_id: Slug, body: EntitlementIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        tenant = await _guard(conn, caller, f"provider-entitlement:{provider_id}")
        if await store.live(conn, provider_id, body.service_code) is not None:
            raise ZidError(
                "ENTITLEMENT_ALREADY_LIVE",
                "A live entitlement already exists for this provider and service; revoke it before recording another.",
                http_status=409,
            )
        ident = await store.insert(
            conn,
            tenant_id=tenant,
            created_by=str(caller.ctx.principal_id or tenant),
            provider_id=provider_id,
            service_code=body.service_code,
            countries=sorted(set(body.countries)),
            purposes=sorted(set(body.purposes)),
            regions=sorted(set(body.processing_regions)),
            valid_from=body.valid_from,
            valid_until=body.valid_until,
            assurance=body.assurance,
            cost_rank=body.cost_rank,
        )
        rows = [
            r for r in await store.listing(conn, provider_id) if str(r["entitlement_id"]) == ident
        ]
    return {**_ids(), **_public(rows[0]), "limitations": LIMITATIONS}


@router.get("/v2/providers/entitlements")
async def list_entitlements(
    request: Request, caller: CallerDep, provider_id: Slug | None = None
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _guard(conn, caller, "provider-entitlement:list")
        rows = await store.listing(conn, provider_id)
    return {**_ids(), "entitlements": [_public(r) for r in rows], "limitations": LIMITATIONS}


async def _change(
    request: Request,
    caller: Caller,
    provider_id: str,
    service: str,
    status: str,
    reason: str,
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        tenant = await _guard(conn, caller, f"provider-entitlement:{provider_id}")
        try:
            row = await change_status(conn, tenant, provider_id, service, status, reason)
        except EntitlementNotFound as e:
            raise ZidError(
                "ENTITLEMENT_NOT_FOUND",
                "There is no live entitlement for this provider and service.",
                http_status=404,
            ) from e
    return {**_ids(), **_public(row), "limitations": LIMITATIONS}


@router.post("/v2/providers/{provider_id}/entitlements/{service}/suspend")
async def suspend_entitlement(
    provider_id: Slug, service: Slug, body: ReasonIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _change(request, caller, provider_id, service, "SUSPENDED", body.reason)


@router.post("/v2/providers/{provider_id}/entitlements/{service}/revoke")
async def revoke_entitlement(
    provider_id: Slug, service: Slug, body: ReasonIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _change(request, caller, provider_id, service, "REVOKED", body.reason)


@router.get("/v2/providers/{provider_id}/entitlements/{service}/impact")
async def withdrawal_impact(
    provider_id: Slug, service: Slug, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """Impact analysis before a kill switch is pulled (Doc 03 RCG-G08). Changes nothing."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _guard(conn, caller, f"provider-entitlement:{provider_id}")
        live = await store.live(conn, provider_id, service)
        if live is None:
            raise ZidError(
                "ENTITLEMENT_NOT_FOUND",
                "There is no live entitlement for this provider and service.",
                http_status=404,
            )
        rows = [r for r in await store.listing(conn, None) if r["status"] != "REVOKED"]
    domain = [store.to_domain(r) for r in rows]
    target = store.to_domain(live)
    try:
        impacts = pa.withdrawal_impact(domain, target, datetime.now(UTC))
    except pa.ImpactTooLarge as e:
        raise ZidError(
            "IMPACT_TOO_LARGE",
            "This entitlement covers too many scopes to analyse at once; narrow it first.",
            http_status=422,
        ) from e
    counts: dict[str, int] = {}
    for i in impacts:
        counts[i.outcome] = counts.get(i.outcome, 0) + 1
    return {
        **_ids(),
        "provider_id": provider_id,
        "service_code": service,
        "summary": counts,
        "scopes": [
            {
                "country": i.country,
                "purpose": i.purpose,
                "processing_region": i.region,
                "outcome": i.outcome,
                "replacement_provider_id": i.after,
                "refusals": [r.value for r in i.refusals_after],
            }
            for i in impacts
            if i.outcome != "UNAFFECTED"
        ],
        "limitations": [
            *LIMITATIONS,
            "LOST means no lawful provider would remain: callers would be refused, never routed to an unentitled one.",
        ],
    }


@router.post("/v2/providers/route")
async def dry_run_route(body: RouteIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _guard(conn, caller, "provider-entitlement:route")
        rows = [r for r in await store.listing(conn, None) if r["status"] != "REVOKED"]
    entitlements = [store.to_domain(r) for r in rows if r["service_code"] == body.service_code]
    failed = next((e for e in entitlements if e.provider_id == body.failing_over_from), None)
    decision = pa.route(
        entitlements,
        pa.RouteRequest(
            country=body.country,
            service_code=body.service_code,
            purpose=body.purpose,
            region=body.processing_region,
            required_assurance=body.required_assurance,
        ),
        datetime.now(UTC),
        failing_over_from=failed,
        downgrade_approval_ref=body.downgrade_approval_ref,
    )
    return {
        **_ids(),
        "provider_id": decision.provider_id,
        "refusals": [r.value for r in decision.refusals],
        "considered": {p: [r.value for r in refs] for p, refs in decision.considered.items()},
        "approved_downgrade": decision.approved_downgrade,
        "limitations": LIMITATIONS,
    }
