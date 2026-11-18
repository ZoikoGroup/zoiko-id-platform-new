"""WF-05 address and proof of address on a case, tenant API (ZID-DTV-WF-05 §19, A05-A07, A10; CR-115).

    PUT  /v1/verification-cases/{id}/address                                   the address as typed (a new revision)        verification:execute
    POST /v1/verification-cases/{id}/address-documents                         a proof of address document, assessed        verification:execute
    GET  /v1/verification-cases/{id}/address-documents                         the documents and where their review is       verification:read
    POST /v1/verification-cases/{id}/address-documents/{doc}:review            the maker's finding                            verification:review
    POST /v1/verification-cases/{id}/address-documents/{doc}:check             a second, different person                     verification:review
    POST /v1/verification-cases/{id}/postal-challenges                         post a one-time code to the address (A08)      verification:execute
    POST /v1/verification-cases/{id}/postal-challenges/{cid}:confirm           the code from the letter                       verification:execute

The document file itself is an evidence upload (`/v2/evidence/uploads`): this API keeps only its reference and what was read from it. Nothing here says
"verified": a document is reviewed, and an association needs two people.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verification.api.verification import _scope
from zid_platform.modules.verification.application import address_checks as svc
from zid_platform.modules.verification.domain import address as ad
from zid_platform.modules.verification.domain import address_review as ar
from zid_platform.modules.verification.domain import check_cases as cc

log = logging.getLogger("zid.verification.address")
router = APIRouter(tags=["verification"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
Id = Annotated[str, Path(pattern=r"^[0-9a-fA-F-]{36}$")]
_STATUS = {"CASE_NOT_FOUND": 404, "DOCUMENT_NOT_FOUND": 404, "CASE_CLOSED": 409, "METHOD_NOT_AVAILABLE_FOR_CASE": 409, "CHECKER_IS_MAKER": 403,
           "REVIEW_ALREADY_DECIDED": 409, "NOTHING_TO_CHECK": 409, "CHALLENGE_NOT_FOUND": 404, "POSTAL_NOT_CONFIGURED_FOR_MARKET": 409, "ADDRESS_REQUIRED": 409}  # fmt: skip


class CaseAddressIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line1: str = Field(min_length=1, max_length=120)
    line2: str | None = Field(default=None, max_length=120)
    city: str = Field(min_length=1, max_length=120)
    region: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=120)
    country: str = Field(pattern=r"^[A-Za-z]{2}$")

    subject_name: str | None = Field(
        default=None,
        max_length=200,
        description="Only for a licensed association source; compared, never stored.",
    )

    def raw(self) -> dict[str, Any]:
        return {k: v for k, v in self.model_dump().items() if v is not None and k != "subject_name"}


class AddressDocumentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upload_ref: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_:.-]{7,127}$",
        description="The evidence upload holding the file.",
    )
    doc_type: str = Field(pattern=r"^[A-Z][A-Z_]{2,40}$")
    issuer: str = Field(max_length=120)
    issued_on: date
    name_on_document: str = Field(min_length=1, max_length=200)
    address_on_document: CaseAddressIn
    subject_name: str = Field(
        min_length=1,
        max_length=200,
        description="Compared with the name on the document; never stored.",
    )


class AddressReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    finding: Literal["CONFIRMED", "REQUIRES_MORE_EVIDENCE", "NOT_SUPPORTED"]
    reason_codes: list[str] = Field(default_factory=list, max_length=10)


class AddressCheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agrees: bool
    reason_codes: list[str] = Field(default_factory=list, max_length=10)


def _refuse(e: Exception) -> ZidError:
    code = str(getattr(e, "code", "ADDRESS_REFUSED"))
    return ZidError(
        code if code in _STATUS else "ADDRESS_REFUSED",
        str(e).split(":")[0],
        http_status=_STATUS.get(code, 422),
    )


def _who(caller: Caller) -> str:
    return caller.ctx.principal_id or caller.client_id


@router.put("/v1/verification-cases/{case_id}/address")
async def set_address(
    case_id: Id, body: CaseAddressIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    _scope(caller, "verification:execute")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            return await svc.set_address(
                conn, case_id, body.raw(), now=datetime.now(UTC), locator=getattr(request.app.state, "address_locator", None),
                source=getattr(request.app.state, "address_source", None), subject_name=body.subject_name,
            )  # fmt: skip
        except (cc.CaseError, ad.AddressError) as e:
            raise _refuse(e) from e


@router.post("/v1/verification-cases/{case_id}/address-documents", status_code=201)
async def add_document(
    case_id: Id, body: AddressDocumentIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    _scope(caller, "verification:execute")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            return await svc.add_document(
                conn, case_id, upload_ref=body.upload_ref, doc_type=body.doc_type, issuer=body.issuer, issued_on=body.issued_on,
                name_on_document=body.name_on_document, address_on_document=body.address_on_document.raw(), subject_name=body.subject_name,
                now=datetime.now(UTC),
            )  # fmt: skip
        except (cc.CaseError, ad.AddressError) as e:
            raise _refuse(e) from e


@router.get("/v1/verification-cases/{case_id}/address-documents")
async def documents(case_id: Id, request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:read")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            return {"items": await svc.documents(conn, case_id)}
        except cc.CaseError as e:
            raise _refuse(e) from e


@router.post("/v1/verification-cases/{case_id}/address-documents/{document_id}:review")
async def review(
    case_id: Id, document_id: Id, body: AddressReviewIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    _scope(caller, "verification:review")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            return await svc.review(
                conn, case_id, document_id, finding=ar.Finding(body.finding), reasons=tuple(body.reason_codes), by=_who(caller),
                now=datetime.now(UTC),
            )  # fmt: skip
        except (cc.CaseError, ar.ReviewError) as e:
            raise _refuse(e) from e


@router.post("/v1/verification-cases/{case_id}/address-documents/{document_id}:check")
async def check(
    case_id: Id, document_id: Id, body: AddressCheckIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    _scope(caller, "verification:review")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            return await svc.check(
                conn,
                case_id,
                document_id,
                agrees=body.agrees,
                reasons=tuple(body.reason_codes),
                by=_who(caller),
                now=datetime.now(UTC),
            )
        except (cc.CaseError, ar.ReviewError) as e:
            raise _refuse(e) from e


class PostalCodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=6, max_length=16)


async def _post(request: Request, caller: Caller, dispatch: svc.PostalDispatch) -> None:
    """After the transaction: hand the letter to the deployment's mail house (`app.state.postal_sender`, none ships) and record its reference."""
    sender = getattr(request.app.state, "postal_sender", None)
    if sender is None:
        log.warning(
            "postal code %s not posted: no postal sender is configured", dispatch.challenge_id[:8]
        )
        return
    try:
        ref = str(await sender.send(dispatch))
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await svc.mark_dispatched(
                conn, dispatch.challenge_id, dispatch_ref=ref, now=datetime.now(UTC)
            )
    except Exception:  # noqa: BLE001 - the case can ask for a new code
        log.warning("postal code %s dispatch failed", dispatch.challenge_id[:8], exc_info=False)


@router.post("/v1/verification-cases/{case_id}/postal-challenges", status_code=202)
async def start_postal(case_id: Id, request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:execute")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            view, dispatch = await svc.start_postal(conn, case_id, now=datetime.now(UTC))
        except (cc.CaseError, ad.AddressError) as e:
            raise _refuse(e) from e
    await _post(request, caller, dispatch)
    return view


@router.post("/v1/verification-cases/{case_id}/postal-challenges/{challenge_id}:confirm")
async def confirm_postal(
    case_id: Id, challenge_id: Id, body: PostalCodeIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    _scope(caller, "verification:execute")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            out = await svc.confirm_postal(
                conn, case_id, challenge_id, body.code, now=datetime.now(UTC)
            )
        except cc.CaseError as e:
            raise _refuse(e) from e
    if not out["verified"]:
        raise ZidError(
            "POSTAL_CODE_NOT_ACCEPTED",
            f"The code was not accepted. Attempts left: {out['attempts_left']}.",
            http_status=422,
        )
    return out
