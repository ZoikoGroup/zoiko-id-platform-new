"""The governed CSCA trust store (ZID-DTV-WF-01 section 13, screen O2; CR-107).

    POST /v2/trust-store/csca/packages                    stage an imported master list with its source and the anchors the stager confirmed   (evidence.trust.propose)
    POST /v2/trust-store/csca/packages/{id}:validate      verify the stored list against the stored anchors (a list that does not verify is REJECTED)  (propose)
    POST /v2/trust-store/csca/packages/{id}:check-chip    run a chip check against this one package without trusting it for anyone               (propose)
    POST /v2/trust-store/csca/packages/{id}:approve       a person other than the stager approves a validated package                          (evidence.trust.approve)
    POST /v2/trust-store/csca/packages/{id}:reject        reject with a reason                                                                    (approve)
    POST /v2/trust-store/csca/packages/{id}:activate      a person other than the stager makes an approved package the active trust set          (approve)
    POST /v2/trust-store/csca/rollback                    restore the previously active package, with a reason                                   (approve)
    POST /v2/trust-store/csca/retire                      stop trusting the active package; afterwards no chip is certified until another is activated (approve)
    GET  /v2/trust-store/csca/packages[/{id}]             metadata only: no list bytes, no anchor bodies, no certificate bodies                  (evidence.read)
    GET  /v2/trust-store/csca/active                      which trust a chip check uses for this tenant, and its manifest digest                 (evidence.read)

Importing is not trusting. See `evidence/application/trust_store.py` for the lifecycle and `evidence/domain/trust_store.py` for the rules.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.evidence.api.v2_extract import V2ChipIn
from zid_platform.modules.evidence.application import trust_store as svc
from zid_platform.modules.evidence.application.csca_master_list import MAX_BYTES
from zid_platform.modules.evidence.infrastructure import trust_package_store as store
from zid_platform.modules.evidence.infrastructure.trust_config import trust_config
from zid_platform.protected import protected_action

router = APIRouter(tags=["evidence-trust-store"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]

PROPOSE = "evidence.trust.propose"
APPROVE = "evidence.trust.approve"
RESOURCE = "trust-store:csca"
_B64 = Annotated[str, Field(min_length=8, max_length=(MAX_BYTES * 4) // 3 + 16)]
_STATUS = {
    "PACKAGE_NOT_FOUND": 404,
    "INVALID_STATE": 409,
    "SELF_APPROVAL": 403,
    "SELF_ACTIVATION": 403,
    "NOTHING_TO_ROLL_BACK": 409,
    "NOTHING_TO_RETIRE": 409,
    "NO_PREVIOUS_PACKAGE": 409,
}


class StageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    master_list_base64: _B64
    anchors_base64: list[Annotated[str, Field(min_length=8, max_length=20000)]] = Field(
        min_length=1, max_length=svc.MAX_ANCHORS
    )
    confirmed_anchor_sha256: list[Annotated[str, Field(pattern=r"^[0-9a-fA-F]{64}$")]] = Field(
        min_length=1, max_length=svc.MAX_ANCHORS
    )
    source_uri: str = Field(min_length=3, max_length=500)
    source_note: str | None = Field(default=None, max_length=500)


class ReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=500)


class RollbackIn(ReasonIn):
    to_package_id: uuid.UUID | None = None


def _err(e: svc.TrustStoreError) -> ZidError:
    return ZidError(
        e.code,
        e.detail or e.code.replace("_", " ").capitalize() + ".",
        http_status=_STATUS.get(e.code, 422),
    )


def _b64(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ZidError("VALIDATION_FAILED", "A value is not valid base64.", http_status=422) from e


def _who(caller: Caller) -> tuple[str, str]:
    tenant, actor = caller.ctx.tenant_id, caller.ctx.principal_id
    if not tenant or not actor:
        raise ZidError("FORBIDDEN", "A signed-in person is required.", http_status=403)
    return str(tenant), str(actor)


async def _run(
    request: Request, caller: Caller, action: str, resource: str, work: Any
) -> dict[str, Any]:
    tenant, actor = _who(caller)
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(conn, caller, action=action, resource=resource)
            return await work(conn, tenant, actor, datetime.now(UTC))  # type: ignore[no-any-return]
    except svc.TrustStoreError as e:
        raise _err(e) from e
    except store.StaleVersion as e:
        raise ZidError("CONFLICT", "The package changed; read it again.", http_status=409) from e


@router.post("/v2/trust-store/csca/packages", status_code=201)
async def stage(body: StageIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    master = _b64(body.master_list_base64)
    anchors = [_b64(a) for a in body.anchors_base64]

    async def work(conn: Any, tenant: str, actor: str, now: datetime) -> dict[str, Any]:
        row = await svc.stage(
            conn, tenant_id=tenant, actor=actor, master_list=master, anchors=anchors,
            confirmed_anchor_sha256=body.confirmed_anchor_sha256, source_uri=body.source_uri, source_note=body.source_note, now=now,
        )  # fmt: skip
        return svc.view(row)

    return await _run(request, caller, PROPOSE, f"{RESOURCE}:new", work)


@router.post("/v2/trust-store/csca/packages/{package_id}:validate")
async def validate(package_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def work(conn: Any, tenant: str, actor: str, now: datetime) -> dict[str, Any]:
        return svc.view(
            await svc.validate(
                conn, tenant_id=tenant, actor=actor, package_id=str(package_id), now=now
            )
        )

    return await _run(request, caller, PROPOSE, f"{RESOURCE}:{package_id}", work)


@router.post("/v2/trust-store/csca/packages/{package_id}:check-chip")
async def check_chip(
    package_id: uuid.UUID, body: V2ChipIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    sod = _b64(body.sod_base64)
    groups = {int(k): _b64(v) for k, v in body.data_groups.items()}
    config = getattr(request.app.state, "trust_config", None) or trust_config()

    async def work(conn: Any, tenant: str, actor: str, now: datetime) -> dict[str, Any]:
        return await svc.check_chip(
            conn, package_id=str(package_id), sod=sod, groups=groups, crls=config.crls, at=now
        )

    return await _run(request, caller, PROPOSE, f"{RESOURCE}:{package_id}", work)


@router.post("/v2/trust-store/csca/packages/{package_id}:approve")
async def approve(package_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def work(conn: Any, tenant: str, actor: str, now: datetime) -> dict[str, Any]:
        return svc.view(
            await svc.approve(
                conn, tenant_id=tenant, actor=actor, package_id=str(package_id), now=now
            )
        )

    return await _run(request, caller, APPROVE, f"{RESOURCE}:{package_id}", work)


@router.post("/v2/trust-store/csca/packages/{package_id}:reject")
async def reject(
    package_id: uuid.UUID, body: ReasonIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def work(conn: Any, tenant: str, actor: str, now: datetime) -> dict[str, Any]:
        row = await svc.reject(
            conn,
            tenant_id=tenant,
            actor=actor,
            package_id=str(package_id),
            reason=body.reason,
            now=now,
        )
        return svc.view(row)

    return await _run(request, caller, APPROVE, f"{RESOURCE}:{package_id}", work)


@router.post("/v2/trust-store/csca/packages/{package_id}:activate")
async def activate(package_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def work(conn: Any, tenant: str, actor: str, now: datetime) -> dict[str, Any]:
        return svc.view(
            await svc.activate(
                conn, tenant_id=tenant, actor=actor, package_id=str(package_id), now=now
            )
        )

    return await _run(request, caller, APPROVE, f"{RESOURCE}:{package_id}", work)


@router.post("/v2/trust-store/csca/rollback")
async def rollback(body: RollbackIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def work(conn: Any, tenant: str, actor: str, now: datetime) -> dict[str, Any]:
        row = await svc.rollback(
            conn, tenant_id=tenant, actor=actor, to_package_id=str(body.to_package_id) if body.to_package_id else None,
            reason=body.reason, now=now,
        )  # fmt: skip
        return svc.view(row)

    return await _run(request, caller, APPROVE, f"{RESOURCE}:rollback", work)


@router.post("/v2/trust-store/csca/retire")
async def retire(body: ReasonIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def work(conn: Any, tenant: str, actor: str, now: datetime) -> dict[str, Any]:
        return svc.view(
            await svc.retire(conn, tenant_id=tenant, actor=actor, reason=body.reason, now=now)
        )

    return await _run(request, caller, APPROVE, f"{RESOURCE}:retire", work)


@router.get("/v2/trust-store/csca/packages")
async def list_packages(request: Request, caller: CallerDep) -> dict[str, Any]:
    async def work(conn: Any, tenant: str, actor: str, now: datetime) -> dict[str, Any]:
        return {"items": [svc.view(r) for r in await svc.listing(conn)]}

    return await _run(request, caller, "evidence.read", f"{RESOURCE}:list", work)


@router.get("/v2/trust-store/csca/packages/{package_id}")
async def read_package(
    package_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def work(conn: Any, tenant: str, actor: str, now: datetime) -> dict[str, Any]:
        return svc.view(await svc.read(conn, str(package_id)))

    return await _run(request, caller, "evidence.read", f"{RESOURCE}:{package_id}", work)


@router.get("/v2/trust-store/csca/active")
async def active(request: Request, caller: CallerDep) -> dict[str, Any]:
    config = getattr(request.app.state, "trust_config", None) or trust_config()

    async def work(conn: Any, tenant: str, actor: str, now: datetime) -> dict[str, Any]:
        eff = await svc.effective_csca(conn, config.csca)
        return {**eff.public(), "governed": eff.governed, "certificates": len(eff.csca)}

    return await _run(request, caller, "evidence.read", f"{RESOURCE}:active", work)
