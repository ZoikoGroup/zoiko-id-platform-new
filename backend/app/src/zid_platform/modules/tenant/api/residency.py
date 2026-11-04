"""Tenant residency and sovereignty API (ZID-TENANCY-001 §25; F-W5; CR-014).

    GET  /v1/cells                                  cell catalog: regions, jurisdictions, profiles
    GET  /v1/residency-offers                       the four offers and where Regional can be taken (public, EDR-002)
    GET  /v1/tenant/residency                       tenant.residency.manage (R3; read)
    PUT  /v1/tenant/residency                       tenant.residency.manage; a CHANGE needs a second admin, the FIRST baseline (below) does not
    GET  /v1/tenant/residency-matrix                subsystem placement for the tenant's profile
    GET  /v1/tenant/transfer-rules                  tenant.residency.manage
    POST /v1/tenant/transfer-rules                  tenant.residency.manage + second admin
    POST /v1/tenant/transfer-rules/{id}:suspend     tenant.residency.manage (no approval: safer)
    POST /v1/tenant/cross-cell-transfers:authorize  tenant.residency.manage (dry-run/authorize)

The tenant is always the caller's (server-derived, INV-13); no body names a tenant.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.authentication.ports import require_step_up
from zid_platform.modules.tenant.application import residency as service
from zid_platform.modules.tenant.domain.offers import public_view, require_cell_ready
from zid_platform.modules.tenant.domain.residency import (
    Residency,
    ResidencyError,
    TransferRule,
    coverage_matrix,
)
from zid_platform.modules.tenant.infrastructure import residency_store as store
from zid_platform.modules.tenant.infrastructure.catalog import catalog
from zid_platform.modules.tenant.infrastructure.offers import offers
from zid_platform.modules.tenant.infrastructure.tenant_store import load_tenant
from zid_platform.protected import approved_by_second_admin, protected_action

router = APIRouter(tags=["residency"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
ACTION = "tenant.residency.manage"
_STATUS = {
    "NOT_FOUND": 404,
    "SOVEREIGN_OPERATOR_DENIED": 403,
    "PLACEMENT_MOVE_NOT_ALLOWED": 409,
    "CELL_NOT_READY": 409,
}


def http(e: ResidencyError) -> ZidError:
    return ZidError(
        e.code,
        e.detail or e.code.replace("_", " ").capitalize() + ".",
        http_status=_STATUS.get(e.code, 422),
    )


def residency_view(r: Residency | None) -> dict[str, Any] | None:
    if r is None:
        return None
    return {
        "residency_profile_id": r.profile,
        "home_cell_id": r.home_cell_id,
        "permitted_recovery_cells": list(r.permitted_recovery_cells),
        "prohibited_cells": list(r.prohibited_cells),
        "operator_jurisdictions": list(r.operator_jurisdictions),
        "key_profile": r.key_profile,
        "isolation_mode": r.isolation_mode,
        "jurisdiction_pack_id": r.jurisdiction_pack_id,
        "placement_epoch": r.placement_epoch,
        "revision": r.revision,
    }


def rule_view(r: TransferRule) -> dict[str, Any]:
    return {
        "rule_id": r.rule_id,
        "data_class": r.data_class,
        "destination_cell_id": r.destination_cell_id,
        "purpose_code": r.purpose_code,
        "mechanism": r.mechanism,
        "field_allowlist": list(r.field_allowlist),
        "valid_from": r.valid_from.isoformat(),
        "valid_to": r.valid_to.isoformat() if r.valid_to else None,
        "status": r.status,
    }


@router.get("/v1/cells")
async def list_cells() -> dict[str, Any]:
    return {
        "items": [
            {
                "cell_id": c.cell_id,
                "region": c.region,
                "jurisdiction": c.country,
                "legal_zone": c.legal_zone,
                "supported_profiles": list(c.supported_profiles),
                "approved_recovery_cells": list(c.approved_recovery_cells),
                "recovery_only": c.recovery_only,
            }
            for c in catalog().cells.values()
        ]
    }


async def _session(request: Request, caller: Caller, fn: Any) -> Any:
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            return await fn(conn)
    except ResidencyError as e:
        raise http(e) from e


@router.get("/v1/tenant/residency")
async def get_residency(request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action=ACTION, resource="tenant_residency", change="read"
        )
        return {"residency": residency_view(await store.load(conn, caller.ctx.tenant_id or ""))}

    return await _session(request, caller, go)  # type: ignore[no-any-return]


class ResidencyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    residency_profile_id: str = Field(pattern="^SRP-[0-3]$")
    home_cell_id: str = Field(pattern="^[a-z0-9-]{1,40}$")
    permitted_recovery_cells: list[str] = Field(default_factory=list, max_length=5)
    prohibited_cells: list[str] = Field(default_factory=list, max_length=50)
    operator_jurisdictions: list[str] = Field(default_factory=list, max_length=20)
    key_profile: str = Field(
        default="PLATFORM_MANAGED", pattern="^(PLATFORM_MANAGED|DEDICATED_TENANT_KEY|BYOK|HYOK)$"
    )
    jurisdiction_pack_id: str
    approval_ref: str = Field(min_length=3, max_length=200)  # legal/compliance mapping approval
    approval_request_id: uuid.UUID | None = (
        None  # needed for a change; the first baseline of a one-person organization needs none
    )

    def change(self) -> str:
        return (
            f"{self.residency_profile_id}|{self.home_cell_id}|{','.join(sorted(self.permitted_recovery_cells))}"
            f"|{','.join(sorted(j.upper() for j in self.operator_jurisdictions))}|{self.jurisdiction_pack_id}"
        )


def _environment(request: Request) -> str:
    return str(getattr(request.app.state, "environment", "production"))


@router.get("/v1/residency-offers")
async def residency_offers(request: Request) -> dict[str, Any]:
    o = offers()
    return {
        "version": o.version,
        "offers": public_view(o, environment=_environment(request)),
    }


async def _is_first_baseline(conn: Any, caller: Caller, body: ResidencyBody) -> bool:
    """True only for the very first residency record of a workspace, kept on the home cell it was created on, with no recovery cell added."""
    tenant = caller.ctx.tenant_id or ""
    if await store.load(conn, tenant) is not None or body.permitted_recovery_cells:
        return False
    home = await load_tenant(conn, tenant)
    return home is not None and home.home_cell_id == body.home_cell_id


@router.put("/v1/tenant/residency")
async def put_residency(body: ResidencyBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        try:  # EDR-002 §2: a home cell that has not passed its readiness gate holds the segment, never downgrades
            require_cell_ready(
                offers(),
                body.residency_profile_id,
                body.home_cell_id,
                environment=_environment(request),
            )
        except ResidencyError as e:
            raise http(e) from e
        if await _is_first_baseline(conn, caller, body):
            # ZID-ENG-DEC-010 and the 13C solo variant: confirming where the organization already lives is the owner's own baseline, not a change to a boundary. Nothing
            # moves (same home cell, no recovery cells), the permission and the fresh passkey proof still apply, and every later change needs a second administrator.
            await protected_action(
                conn, caller, action=ACTION, resource="tenant_residency", change=body.change()
            )
        else:
            if body.approval_request_id is None:
                raise ZidError(
                    "APPROVAL_REQUIRED",
                    "Changing residency needs an independent second administrator to approve.",
                    http_status=403,
                )
            await approved_by_second_admin(
                conn,
                caller,
                action=ACTION,
                resource="tenant_residency",
                change=body.change(),
                approval_request_id=str(body.approval_request_id),
            )
        await require_step_up(
            request,
            conn,
            caller,
            action="change-customer-home-region-cross-region-transfer-policy",
            resource="tenant_residency",
            change=body.model_dump(mode="json"),
        )
        r = await service.set_residency(
            conn,
            caller.ctx.tenant_id or "",
            profile=body.residency_profile_id,
            home_cell_id=body.home_cell_id,
            recovery_cells=body.permitted_recovery_cells,
            prohibited_cells=body.prohibited_cells,
            operator_juris=body.operator_jurisdictions,
            key_profile=body.key_profile,
            jurisdiction_pack_id=body.jurisdiction_pack_id,
            approval_ref=body.approval_ref,
            actor=caller.ctx.principal_id or "",
        )
        return {"residency": residency_view(r)}

    return await _session(request, caller, go)  # type: ignore[no-any-return]


@router.get("/v1/tenant/residency-matrix")
async def residency_matrix(request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action=ACTION, resource="tenant_residency", change="read"
        )
        r = await store.load(conn, caller.ctx.tenant_id or "")
        if r is None:
            raise ResidencyError("NOT_FOUND", "no residency profile assigned")
        items = coverage_matrix(catalog(), r)
        return {
            "residency_profile_id": r.profile,
            "items": items,
            "proven": all(i["compliant"] for i in items),
        }

    return await _session(request, caller, go)  # type: ignore[no-any-return]


@router.get("/v1/tenant/transfer-rules")
async def list_rules(request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action=ACTION, resource="transfer_rule:*", change="read"
        )
        return {"items": [rule_view(r) for r in await store.rules(conn)]}

    return await _session(request, caller, go)  # type: ignore[no-any-return]


class RuleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_class: str = Field(pattern="^DC[1-3]$")
    destination_cell_id: str = Field(pattern="^[a-z0-9-]{1,40}$")
    purpose_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    mechanism: str = Field(min_length=2, max_length=64)
    field_allowlist: list[str] = Field(default_factory=list, max_length=100)
    approval_ref: str = Field(min_length=3, max_length=200)
    valid_to: datetime | None = None
    approval_request_id: uuid.UUID

    def change(self) -> str:
        return f"{self.data_class}|{self.destination_cell_id}|{self.purpose_code}|{self.mechanism}"


@router.post("/v1/tenant/transfer-rules", status_code=201)
async def add_rule(body: RuleBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        approver = await approved_by_second_admin(
            conn,
            caller,
            action=ACTION,
            resource="transfer_rule:*",
            change=body.change(),
            approval_request_id=str(body.approval_request_id),
        )
        await require_step_up(
            request,
            conn,
            caller,
            action="change-customer-home-region-cross-region-transfer-policy",
            resource="transfer_rule:*",
            change=body.model_dump(mode="json"),
        )
        r = await service.add_rule(
            conn,
            caller.ctx.tenant_id or "",
            data_class=body.data_class,
            destination_cell_id=body.destination_cell_id,
            purpose_code=body.purpose_code,
            mechanism=body.mechanism,
            field_allowlist=body.field_allowlist,
            approval_ref=body.approval_ref,
            created_by=caller.ctx.principal_id or "",
            approved_by=approver,
            valid_to=body.valid_to,
        )
        return rule_view(r)

    return await _session(request, caller, go)  # type: ignore[no-any-return]


@router.post("/v1/tenant/transfer-rules/{rule_id}:suspend")
async def suspend_rule(rule_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action=ACTION, resource=f"transfer_rule:{rule_id}", change="suspend"
        )
        r = await service.suspend_rule(
            conn, caller.ctx.tenant_id or "", str(rule_id), actor=caller.ctx.principal_id or ""
        )
        return rule_view(r)

    return await _session(request, caller, go)  # type: ignore[no-any-return]


class TransferBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_class: str = Field(pattern="^(DC0|DC1|DC2|DC3|KEY_MATERIAL)$")
    destination_cell_id: str = Field(pattern="^[a-z0-9-]{1,40}$")
    purpose_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")


@router.post("/v1/tenant/cross-cell-transfers:authorize")
async def authorize(body: TransferBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action=ACTION, resource="cross_cell_transfer:*", change="authorize"
        )
        d = await service.authorize_transfer(
            conn,
            caller.ctx.tenant_id or "",
            data_class=body.data_class,
            destination_cell_id=body.destination_cell_id,
            purpose_code=body.purpose_code,
            actor=caller.ctx.principal_id,
        )
        return {
            "allowed": d.allowed,
            "code": d.code,
            "rule_id": d.rule_id,
            "field_allowlist": list(d.field_allowlist),
            "cross_border": d.cross_border,
        }

    return await _session(request, caller, go)  # type: ignore[no-any-return]
