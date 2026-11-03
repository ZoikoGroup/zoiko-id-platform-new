"""Organization onboarding readiness API (ZID-UX-WF-013C screen 1; slice L-13, CR-049).

    GET  /v1/organizations/{id}/onboarding                    the checklist and derived readiness (members of this organization only)
    POST /v1/organizations/{id}/onboarding/acknowledgments    record a versioned acceptance (Owner or Administrator; idempotent)
    POST /v1/organizations/{id}/activation                    evaluate the gate and activate only if it passes

The organization in the path is the caller's own or "no such organization". Nothing the client sends decides readiness: the trust
assertions are read from the services that own them, the required items are checked by the server, and the state is derived each time.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ErrorDetail, ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.onboarding.api.org_claims import _peek as claim_status
from zid_platform.modules.onboarding.application import readiness as svc
from zid_platform.modules.onboarding.domain.readiness import Item
from zid_platform.modules.organization.ports import latest_kyb_case_id

router = APIRouter(tags=["onboarding"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]

_STATUS = {
    "SETUP_NOT_PERMITTED": 403,
    "ORGANIZATION_NOT_FOUND": 404,
    "ACKNOWLEDGEMENT_UNKNOWN": 422,
    "ACKNOWLEDGEMENT_VERSION_STALE": 409,
    "ACTIVATION_BLOCKED": 409,
}


def _org(org_id: uuid.UUID, caller: Caller) -> tuple[str, str]:
    tenant, me = caller.ctx.tenant_id or "", caller.ctx.principal_id or ""
    if not tenant or not me:
        raise ZidError(
            "UNAUTHENTICATED", "An authenticated principal is required.", http_status=401
        )
    if (
        str(org_id) != tenant
    ):  # another organization's id is indistinguishable from one that does not exist
        raise ZidError("ORGANIZATION_NOT_FOUND", "No such organization.", http_status=404)
    return tenant, me


def _fail(e: svc.ReadinessError) -> ZidError:
    return ZidError(
        e.code,
        e.detail or e.code.replace("_", " ").capitalize() + ".",
        http_status=_STATUS.get(e.code, 409),
        details=[ErrorDetail(field="blocker", issue=b) for b in e.blockers] or None,
    )


def _item(i: Item) -> dict[str, Any]:
    return {
        "key": i.key,
        "title": i.title,
        "status": i.word,  # Required, In review, Verified, Needs action, Restricted
        "required": i.required,
        "done": i.done,
        "reason": i.reason,
        "next_action": i.next_action,
    }


async def _claim(conn: Any, tenant: str, now: datetime) -> dict[str, Any] | None:
    case_id = await latest_kyb_case_id(conn, tenant)
    return await claim_status(conn, tenant, case_id, now) if case_id else None


def _view(g: svc.Gathered, tenant: str) -> dict[str, Any]:
    p, texts = g.projection, svc.setup_policy().get("acknowledgements") or {}
    return {
        "organization_id": tenant,
        "readiness": p.state.value,
        "trust": [
            _item(i) for i in p.trust
        ],  # read-only canonical assertions: the customer cannot edit any of them
        "required": [_item(i) for i in p.required],
        "approvers": _item(p.approvers),
        "solo": p.solo,
        "can_activate": p.can_activate,
        "blockers": list(p.blockers),
        "restrictions": list(p.restrictions),
        "activated_at": g.activation["activated_at"].isoformat() if g.activation else None,
        "region": g.region,
        "acknowledgements": [
            {
                "kind": k,
                "version": v["version"],
                "title": v["title"],
                "summary": v["summary"],
                "accepted": g.acknowledged.get(k, False),
            }
            for k, v in texts.items()
        ],
        "policy_version": str(svc.setup_policy().get("version", "")),
    }


@router.get("/v1/organizations/{org_id}/onboarding")
async def onboarding(org_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant, me = _org(org_id, caller)
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            await svc.require_member(conn, me)
            got = await svc.gather(conn, tenant, me, await _claim(conn, tenant, now), now=now)
        except svc.ReadinessError as e:
            raise _fail(e) from e
    return _view(got, tenant)


class SetupAckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["ORGANIZATIONAL_CONTROLS", "REGION_POLICY"]
    version: str = Field(min_length=1, max_length=40)
    locale: str | None = Field(default=None, max_length=35)


@router.post("/v1/organizations/{org_id}/onboarding/acknowledgments", status_code=201)
async def acknowledge(
    org_id: uuid.UUID,
    body: SetupAckBody,
    request: Request,
    caller: CallerDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=128)] = None,
) -> dict[str, Any]:
    tenant, me = _org(org_id, caller)
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            created = await svc.acknowledge(
                conn,
                tenant,
                me,
                kind=body.kind,
                version=body.version,
                locale=body.locale,
                idempotency_key=idempotency_key,
                now=now,
            )
            got = await svc.gather(conn, tenant, me, await _claim(conn, tenant, now), now=now)
        except svc.ReadinessError as e:
            raise _fail(e) from e
    return {"recorded": created, **_view(got, tenant)}


@router.post("/v1/organizations/{org_id}/activation")
async def activation(org_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant, me = _org(org_id, caller)
    now = datetime.now(UTC)
    refusal: ZidError | None = None
    out: dict[str, Any] | None = None
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            got, created = await svc.activate(
                conn, tenant, me, await _claim(conn, tenant, now), now=now
            )
            out = {"activated_now": created, **_view(got, tenant)}
        except svc.ReadinessError as e:
            if not e.persist:
                raise _fail(e) from e
            refusal = _fail(e)  # the refusal was audited: commit it, then refuse
    if refusal is not None:
        raise refusal
    assert out is not None
    return out
