"""Evidence packages for a screening case, operator plane (ZID-DTV-WF-06 S13, ENG-16; CR-113).

    POST /v1/screening/review/cases/{id}/exports        ask for a purpose-bound package; without sensitive material it is built now, with it, it waits   (screening.evidence.request)
    POST /v1/screening/review/exports/{id}:approve      a second person, not the requester, approves a package with sensitive material                    (screening.evidence.approve)
    GET  /v1/screening/review/exports/{id}              the package while it is ready and in date, checked for tampering and personal fields as it leaves   (either; audited)

A package states its purpose, names its requester, is watermarked, expires (at most 30 days), and carries a manifest hash over exactly what it holds. Personal fields are hashes, never values, even after
approval, so the package cannot be used as a raw download. Records carry ids, codes and hashes, never a name.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import in_tenant, tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import request_engine
from zid_platform.modules.operator_access.api.screening_sources import sc
from zid_platform.modules.operator_access.ports import require_operator
from zid_platform.operator_actions import Actions

REQUEST = "screening.evidence.request"
APPROVE = "screening.evidence.approve"
EITHER = (REQUEST, APPROVE)
IdPath = Annotated[uuid.UUID, Path()]

router = APIRouter(tags=["screening-exports"])
OperatorDep = Annotated[Any, Depends(require_operator)]

actions = Actions(
    namespace=uuid.UUID("d41c9a72-8b05-4e3d-b6f1-27a0c3e95d68"),
    refused_event="screening.export.refused",
    status={
        "CASE_NOT_FOUND": 404,
        "EXPORT_NOT_FOUND": 404,
        "EXPORT_NOT_PENDING": 409,
        "EXPORT_NOT_READY": 409,
        "EXPORT_EXPIRED": 410,
        "PURPOSE_REQUIRED": 422,
        "EXPIRY_INVALID": 422,
        "SECOND_APPROVER_REQUIRED": 403,
        "REQUESTER_IS_THE_APPROVER": 403,
        "KIND_NOT_EXPORTABLE": 422,
        "EXPORT_TOO_LARGE": 422,
    },
    errors=lambda: (sc.ScreeningError,),
    refused_code="SCREENING_EXPORT_REFUSED",
    capability_hint="a screening evidence capability",
)


class ExportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: str = Field(min_length=10, max_length=500)
    include_sensitive: bool = False
    ttl_days: int = Field(default=7, ge=1, le=30)


def _max_age(request: Request) -> timedelta:
    cfg = getattr(request.app.state, "screening_config", None) or sc.screening_config()
    return cfg.policy.list_max_age  # type: ignore[no-any-return]


@router.post("/v1/screening/review/cases/{request_id}/exports", status_code=201)
async def request_export(
    request_id: IdPath, body: ExportIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        tenant = await sc.screening_requests.locate(conn, str(request_id))
        if tenant is None:
            raise sc.ScreeningError("CASE_NOT_FOUND", str(request_id))
        async with in_tenant(conn, tenant):
            out = await sc.evidence_exports.request_export(
                conn,
                str(request_id),
                requester=str(op.principal_id),
                purpose=body.purpose,
                include_sensitive=body.include_sensitive,
                ttl=timedelta(days=body.ttl_days),
                now=now,
                max_age=_max_age(request),
            )
        return (
            out,
            out["export_id"],
            {
                "case": str(request_id),
                "state": out["state"],
                "sensitive": body.include_sensitive,
                "ttl_days": body.ttl_days,
            },
        )

    return await actions.act(
        request, op, event="screening.export.requested", action="request_export", capability=REQUEST, target_type="screening_export", target=str(request_id), work=work,
    )  # fmt: skip


@router.post("/v1/screening/review/exports/{export_id}:approve")
async def approve_export(export_id: IdPath, request: Request, op: OperatorDep) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        found = await sc.evidence_exports.export_tenant(conn, str(export_id))
        if found is None:
            raise sc.ScreeningError("EXPORT_NOT_FOUND", str(export_id))
        tenant, _case = found
        async with in_tenant(conn, tenant):
            requester = await sc.evidence_exports.requester_of(conn, str(export_id))
            if requester == str(op.principal_id):
                raise sc.ScreeningError(
                    "REQUESTER_IS_THE_APPROVER",
                    "the person who asked for the package cannot approve its sensitive material",
                )
            out = await sc.evidence_exports.approve_export(
                conn,
                str(export_id),
                approver=str(op.principal_id),
                now=now,
                max_age=_max_age(request),
            )
        return out, str(export_id), {"state": out["state"], "manifest_hash": out["manifest_hash"]}

    return await actions.act(
        request, op, event="screening.export.approved", action="approve_export", capability=APPROVE, target_type="screening_export", target=str(export_id), work=work,
    )  # fmt: skip


@router.get("/v1/screening/review/exports/{export_id}")
async def fetch_export(export_id: IdPath, request: Request, op: OperatorDep) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need_any(conn, op, EITHER, f"regulatory:{export_id}")
        found = await sc.evidence_exports.export_tenant(conn, str(export_id))
        if found is None:
            raise ZidError("EXPORT_NOT_FOUND", "There is no such package.", http_status=404)
        try:
            async with in_tenant(conn, found[0]):
                out = await sc.evidence_exports.fetch(conn, str(export_id), now=now)
        except sc.ScreeningError as e:
            raise actions.fail(e) from e
        problems = sc.evidence_exports.verify_stored(out["bundle"], now=now)
        await actions.audit(
            conn, op, event="screening.export.fetched", action="read_export", target_type="screening_export", target=str(export_id),
            outcome="SUCCESS", now=now, details={"manifest_hash": out["manifest_hash"], "problems": len(problems)}, once=False,
        )  # fmt: skip
        return {**out, "verified": not problems, "problems": problems}


__all__ = ["router"]
