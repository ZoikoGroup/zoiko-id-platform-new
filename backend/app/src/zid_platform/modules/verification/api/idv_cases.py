"""Identity proofing cases (ZID-DTV-WF-03 sections 6, 10, 18; CR-110).

    POST /v1/idv/cases                          open a case for a market and route (CHIP, or an alternative the market's signed policy names)
    GET  /v1/idv/cases                          the company's cases: decision, reasons, signal states
    GET  /v1/idv/cases/{id}                     one case, with every decision it has reached
    POST /v1/idv/cases/{id}/nonce               a one-time value the next capture must carry
    POST /v1/idv/cases/{id}/nfc-state           the phone has no NFC, the person declined, or the read failed
    POST /v1/idv/cases/{id}/chip                what the chip returned; the platform judges it (passive authentication)
    POST /v1/idv/cases/{id}/capture             a capture's envelope: nonce, media digest, camera and device facts
    POST /v1/idv/cases/{id}/optical             the printed-document checks' results
    POST /v1/idv/cases/{id}/consent             the notice shown and, where it is the basis, consent
    POST /v1/idv/cases/{id}/consent:withdraw    withdraw it
    POST /v1/idv/cases/{id}/reviews             a reviewer signs an alternative-route case          (verification.idv.review)
    POST /v1/idv/cases/{id}:abandon             the applicant leaves

No route lets a caller state a result: signals come from the platform's own checks, and the decision is re-derived by the pinned rules. A market with no policy in `config/idv/market-policies.yaml` cannot open a case.
Responses carry codes and states only: no document data, no image, no score."""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.evidence.ports import effective_csca, trust_config, verify_chip
from zid_platform.modules.verification.application import idv_case as svc
from zid_platform.modules.verification.domain.idv_decision import ChipAuth, NfcAccess, assertions
from zid_platform.modules.verification.domain.idv_evidence import Envelope
from zid_platform.modules.verification.domain.idv_policy import MarketRules, PolicyError
from zid_platform.modules.verification.infrastructure import idv_policy_file
from zid_platform.protected import protected_action

router = APIRouter(tags=["idv"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]

_STATUS = {
    "CASE_NOT_FOUND": 404,
    "CASE_CLOSED": 409,
    "ROUTE_NOT_ALLOWED_BY_POLICY": 422,
    "ROUTE_HAS_NO_CHIP": 409,
    "BIOMETRICS_OFF": 409,
    "NO_REFERENCE_PORTRAIT": 409,
    "REVIEWER_IS_THE_APPLICANT": 403,
    "ALREADY_REVIEWED": 409,
    "RETRY_CEILING_REACHED": 429,
    "RETRY_CEILING_NOT_CONFIGURED": 409,
    "INVALID_ACCESS_STATE": 422,
}


def _fail(e: svc.CaseError) -> ZidError:
    return ZidError(e.code, e.detail or e.code, http_status=_STATUS.get(e.code, 422))


def _scope(caller: Caller, scope: str) -> None:
    if scope not in caller.scope:
        raise ZidError("SCOPE_INSUFFICIENT", f"{scope} is required.", http_status=403)


def _rules(request: Request, market: str) -> MarketRules:
    source = getattr(request.app.state, "idv_policies", None)
    try:
        table = source() if callable(source) else idv_policy_file.load()
    except PolicyError as e:
        raise ZidError("IDV_POLICY_FILE_INVALID", str(e), http_status=503, retryable=True) from e
    rules = table.get(market.upper())
    if rules is None:
        raise ZidError(
            "MARKET_POLICY_NOT_PUBLISHED",
            "No proofing policy is published for that market.",
            http_status=422,
        )
    return rules


async def _own(conn: Any, case_id: str, caller: Caller) -> svc.Case:
    c = await svc.get(conn, case_id)
    if c is None:
        raise ZidError("CASE_NOT_FOUND", "No such case.", http_status=404)
    if c.applicant != str(caller.ctx.principal_id):
        raise ZidError(
            "NOT_YOUR_CASE", "Only the applicant adds evidence to a case.", http_status=403
        )
    return c


def view(c: svc.Case) -> dict[str, Any]:
    s = c.signals
    return {
        "case_id": c.case_id,
        "market": c.market,
        "route": c.route,
        "state": c.state,
        "decision": c.decision.value,
        "reasons": list(c.reasons),
        "assertions": list(assertions(s)),
        "signals": {
            "optical": s.optical.value, "nfc_read": s.nfc.value, "passive_auth": s.passive_auth.value, "chip_auth": s.chip_auth.value,
            "liveness": s.liveness.value, "face_match": s.face.value,
        },
        "policy_version": c.policy_version,
        "policy_hash": c.policy_hash,
        "reviewers": len(s.reviewers),
        "attempts": c.attempts,
        "version": c.version,
        "created_at": c.created_at.isoformat(),
        "updated_at": c.updated_at.isoformat(),
    }  # fmt: skip


def _b64(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ZidError(
            "VALIDATION_FAILED", "The chip data is not valid base64.", http_status=422
        ) from e


class OpenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market: str = Field(pattern=r"^[A-Za-z0-9_-]{2,12}$")
    route: str = Field(default="CHIP", min_length=1, max_length=60)


class NfcStateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    access: Literal["UNSUPPORTED", "USER_DECLINED", "READ_FAILED"]


class ChipIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sod_base64: str = Field(min_length=8, max_length=(64 * 1024 * 4) // 3 + 16)
    data_groups: dict[
        Annotated[str, Field(pattern=r"^([1-9]|1[0-6])$")],
        Annotated[str, Field(min_length=4, max_length=(256 * 1024 * 4) // 3 + 16)],
    ] = Field(min_length=1, max_length=16)
    printed: (
        dict[Literal["surname", "given_names", "document_number", "birth_date", "expiry_date"], str]
        | None
    ) = None


class CaptureIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nonce: str = Field(min_length=8, max_length=128)
    media_sha256: str = Field(min_length=64, max_length=64)
    captured_at: datetime
    virtual_camera: bool | None = None
    device_attested: bool | None = None
    channel: Literal["APP", "WEB"] = "APP"


class OpticalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: dict[
        Annotated[str, Field(pattern=r"^[A-Z_]{2,40}$")], Literal["PASS", "FAIL", "INCONCLUSIVE"]
    ] = Field(max_length=40)
    typed_transcription: bool = False


class ConsentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notice_version: str = Field(min_length=1, max_length=80)
    consented: bool = False


@router.post("/v1/idv/cases", status_code=201)
async def open_case(body: OpenIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:execute")
    rules = _rules(request, body.market)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            c = await svc.open_case(
                conn,
                tenant_id=caller.ctx.tenant_id or "",
                applicant=str(caller.ctx.principal_id),
                route=body.route,
                rules=rules,
                now=datetime.now(UTC),
            )
        except svc.CaseError as e:
            raise _fail(e) from e
    return view(c)


@router.get("/v1/idv/cases")
async def list_cases(request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:read")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        items = await svc.list_cases(conn)
    return {"items": [view(c) for c in items]}


@router.get("/v1/idv/cases/{case_id}")
async def read_case(case_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:read")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        c = await svc.get(conn, str(case_id))
        if c is None:
            raise ZidError("CASE_NOT_FOUND", "No such case.", http_status=404)
        hist = await svc.history(conn, str(case_id))
    return {
        **view(c),
        "history": [
            {
                "version": h["version"],
                "decision": h["decision"],
                "reasons": h["reasons"],
                "trigger": h["trigger"],
                "decided_at": h["decided_at"].isoformat(),
            }
            for h in hist
        ],
    }


async def _mutate(
    request: Request, caller: Caller, case_id: uuid.UUID, fn: Any, *, own: bool = True
) -> dict[str, Any]:
    _scope(caller, "verification:execute")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        existing = await (_own(conn, str(case_id), caller) if own else svc.get(conn, str(case_id)))
        if existing is None:
            raise ZidError("CASE_NOT_FOUND", "No such case.", http_status=404)
        rules = _rules(request, existing.market)
        if rules.policy_hash != existing.policy_hash:
            # the file changed after the case opened: the case keeps deciding under the rules it was pinned to, but only while they still exist unchanged
            raise ZidError(
                "POLICY_CHANGED_SINCE_OPENED",
                "This case was opened under rules that have since changed; open a new case.",
                http_status=409,
            )
        try:
            c = await fn(conn, rules)
        except svc.CaseError as e:
            raise _fail(e) from e
    return view(c)


@router.post("/v1/idv/cases/{case_id}/nonce")
async def nonce(case_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:execute")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _own(conn, str(case_id), caller)
        try:
            value, c = await svc.issue_nonce(conn, str(case_id), datetime.now(UTC))
        except svc.CaseError as e:
            raise _fail(e) from e
    return {
        "case_id": c.case_id,
        "nonce": value,
        "issued_at": (c.nonce_issued_at or datetime.now(UTC)).isoformat(),
    }


@router.post("/v1/idv/cases/{case_id}/nfc-state")
async def nfc_state(
    case_id: uuid.UUID, body: NfcStateIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _mutate(
        request,
        caller,
        case_id,
        lambda conn, rules: svc.chip_not_read(
            conn, str(case_id), rules, access=NfcAccess(body.access), now=datetime.now(UTC)
        ),
    )


@router.post("/v1/idv/cases/{case_id}/chip")
async def chip(
    case_id: uuid.UUID, body: ChipIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    sod = _b64(body.sod_base64)
    groups = {int(k): _b64(v) for k, v in body.data_groups.items()}
    config = getattr(request.app.state, "trust_config", None) or trust_config()
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        effective = await effective_csca(conn, config.csca)
    verdict = verify_chip(sod, groups, csca=effective.csca, crls=config.crls)
    document = verdict.detail.get("document")
    doc = (
        {k: str(v) for k, v in document.items() if v is not None}
        if isinstance(document, dict)
        else None
    )

    async def go(conn: Any, rules: MarketRules) -> svc.Case:
        return await svc.submit_chip(
            conn, str(case_id), rules, status=verdict.status, reasons=verdict.reasons, document=doc, printed=dict(body.printed) if body.printed else None,
            chip_auth=ChipAuth.NOT_TESTED, now=datetime.now(UTC),
        )  # fmt: skip

    return await _mutate(request, caller, case_id, go)


@router.post("/v1/idv/cases/{case_id}/capture")
async def capture(
    case_id: uuid.UUID, body: CaptureIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    env = Envelope(
        body.nonce,
        body.media_sha256.lower(),
        body.captured_at,
        body.virtual_camera,
        body.device_attested,
    )
    return await _mutate(
        request,
        caller,
        case_id,
        lambda conn, rules: svc.submit_capture(
            conn, str(case_id), rules, env, now=datetime.now(UTC), web=body.channel == "WEB"
        ),
    )


@router.post("/v1/idv/cases/{case_id}/optical")
async def optical(
    case_id: uuid.UUID, body: OpticalIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _mutate(
        request, caller, case_id,
        lambda conn, rules: svc.submit_optical(conn, str(case_id), rules, dict(body.results), now=datetime.now(UTC), typed_transcription=body.typed_transcription),
    )  # fmt: skip


@router.post("/v1/idv/cases/{case_id}/consent")
async def consent(
    case_id: uuid.UUID, body: ConsentIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _mutate(
        request, caller, case_id,
        lambda conn, rules: svc.record_notice_and_consent(conn, str(case_id), rules, notice_version=body.notice_version, consented=body.consented, now=datetime.now(UTC)),
    )  # fmt: skip


@router.post("/v1/idv/cases/{case_id}/consent:withdraw")
async def withdraw(case_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _mutate(
        request,
        caller,
        case_id,
        lambda conn, rules: svc.withdraw_consent(conn, str(case_id), rules, now=datetime.now(UTC)),
    )


@router.post("/v1/idv/cases/{case_id}:abandon")
async def abandon(case_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _mutate(
        request,
        caller,
        case_id,
        lambda conn, rules: svc.abandon(conn, str(case_id), rules, now=datetime.now(UTC)),
    )


@router.post("/v1/idv/cases/{case_id}/reviews")
async def review(case_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:execute")
    me = str(caller.ctx.principal_id)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="verification.idv.review", resource=f"idv_case:{case_id}"
        )
        existing = await svc.get(conn, str(case_id))
        if existing is None:
            raise ZidError("CASE_NOT_FOUND", "No such case.", http_status=404)
        rules = _rules(request, existing.market)
        if rules.policy_hash != existing.policy_hash:
            raise ZidError(
                "POLICY_CHANGED_SINCE_OPENED",
                "This case was opened under rules that have since changed.",
                http_status=409,
            )
        try:
            c = await svc.add_reviewer(
                conn, str(case_id), rules, reviewer=me, now=datetime.now(UTC)
            )
        except svc.CaseError as e:
            raise _fail(e) from e
    return view(c)
