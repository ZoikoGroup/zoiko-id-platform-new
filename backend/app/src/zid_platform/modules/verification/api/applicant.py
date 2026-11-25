"""The person's own hosted verification (ZID-CP-WF01 sections 05, 08, 10; screens S01 to S12). No sign-in: a one-use launch link, then a session-bound credential.

    POST /v2/verification-sessions/launch                         spend the one-use link; returns the session credential (X-Zoiko-Applicant-Token)
    GET  /v2/verification-sessions/{ref}/public-context           who asks, why, the notice, the methods this person may use here, the state
    POST /v2/verification-sessions/{ref}/notices                  the person acknowledged the notice, in a language, and agreed or not to a face comparison
    POST /v2/verification-sessions/{ref}/method-select            choose an eligible method; opens the proofing case pinned to the market's rules
    POST /v2/verification-sessions/{ref}/capture-intents          a one-time, short-lived value the next capture must carry
    POST /v2/verification-sessions/{ref}/evidence/document-image  a document photograph (and, on the face route, a live photograph)
    POST /v2/verification-sessions/{ref}/evidence/chip            what the passport chip returned
    POST /v2/verification-sessions/{ref}/evidence/nfc-state       the phone has no NFC, the person declined, or the read failed
    POST /v2/verification-sessions/{ref}/submit                   the one final submission; a repeat changes nothing
    GET  /v2/verification-sessions/{ref}/public-status            the state, a reference and one safe message; never a reason code or a signal
    POST /v2/verification-sessions/{ref}/handoff                  a one-use link to continue on another device (QR), never carrying personal data
    POST /v2/verification-sessions/{ref}/applicant-cancel         the person leaves
and, for the business's server (signed in):
    GET  /v2/verification-sessions/{ref}/decision                 the normalized result and its digest (resume links: privacy/api/v2_sessions.py)

Evidence goes through the same proofing routes a signed-in applicant uses, so every check, ceiling and gate is the same; only the platform's refusals are
translated into the section 10 codes the person may be shown.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import RequestContext, current_context
from zid_db import tenant_transaction
from zid_errors import ZidError
from zid_outbox import emit

from zid_platform.api_auth import Caller, authenticated_caller, request_engine, unsigned_caller
from zid_platform.country_gate import enforce_regulated, environment_of
from zid_platform.modules.privacy.ports import (
    PrivacyError,
    acknowledge_notice,
    applicant_session,
    cancel_session,
    record_session_consent,
    session_status,
)
from zid_platform.modules.tenant.ports import load_tenant, verified_domains
from zid_platform.modules.verification.api import idv_cases
from zid_platform.modules.verification.application import applicant_flow as flow
from zid_platform.modules.verification.application import idv_case as svc
from zid_platform.modules.verification.domain import applicant as ap
from zid_platform.modules.verification.domain.countries import SANDBOX_ENVIRONMENTS, Capability

router = APIRouter(tags=["verification-applicant"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
TOKEN_HEADER = "X-Zoiko-Applicant-Token"
_PRIVACY_STATUS = {
    "TOKEN_INVALID": 401,
    "TOKEN_ALREADY_USED": 401,
    "SESSION_EXPIRED": 410,
    "SESSION_CANCELED": 409,
    "NOT_FOUND": 404,
}


def _ctx(tenant_id: str, principal: str | None) -> RequestContext:
    base = current_context() or RequestContext(
        request_id=f"req_{uuid.uuid4().hex}",
        correlation_id=f"cor_{uuid.uuid4().hex}",
        service="zid-platform",
        cell_id="local",
    )
    return base.with_identity(tenant_id, principal)


def _privacy(e: PrivacyError) -> ZidError:
    code = ap.FROM_ERROR.get(e.code, e.code)
    return ZidError(
        code,
        ap.MESSAGES[code][1] if code in ap.MESSAGES else (e.detail or code),
        http_status=_PRIVACY_STATUS.get(e.code, 422),
    )


def _safe(e: ZidError) -> ZidError:
    """The platform's own refusal, as the person may be told it (section 10): a code from the fixed list and its sentence, never an internal detail."""
    code = ap.FROM_ERROR.get(e.code)
    if code is None:
        return e  # already a person-safe validation or state message
    return ZidError(code, ap.MESSAGES[code][1], http_status=e.http_status, retryable=e.retryable)


@dataclass(frozen=True, slots=True)
class Applicant:
    tenant: str  # derived from the session credential, never from what the caller sends
    session: Any
    caller: Caller  # the person, acting on their own proofing case only

    @property
    def case_id(self) -> uuid.UUID:
        if self.session.case_id is None:
            raise ZidError(
                "METHOD_NOT_SELECTED", "Choose how to confirm your identity first.", http_status=409
            )
        return uuid.UUID(str(self.session.case_id))


async def applicant(
    session_ref: str,
    request: Request,
    token: Annotated[str | None, Header(alias=TOKEN_HEADER)] = None,
) -> Applicant:
    """The session credential, checked on every call and bound to the session in the path. Never a sign-in, never a server credential."""
    if not token:
        raise ZidError("TOKEN_INVALID", "A session credential is required.", http_status=401)
    try:
        tenant = applicant_session.tenant_of(token)
        async with tenant_transaction(request_engine(request), _ctx(tenant, None)) as conn:
            s = await applicant_session.resolve(conn, token, datetime.now(UTC))
    except PrivacyError as e:
        raise _privacy(e) from e
    if s.session_ref != session_ref or s.applicant_id is None:
        raise ZidError("TOKEN_INVALID", "The session credential is not valid.", http_status=401)
    ctx = _ctx(tenant, str(s.applicant_id))
    caller = unsigned_caller(ctx, client_id="hosted-applicant", scope=frozenset({"verification:execute"}))
    return Applicant(tenant, s, caller)


ApplicantDep = Annotated[Applicant, Depends(applicant)]


async def _tx[T](
    request: Request, a: Applicant, fn: Callable[[AsyncConnection], Awaitable[T]]
) -> T:
    try:
        async with tenant_transaction(request_engine(request), a.caller.ctx) as conn:
            return await fn(conn)
    except PrivacyError as e:
        raise _privacy(e) from e


async def _event(conn: AsyncConnection, a: Applicant, name: str, data: dict[str, Any]) -> None:
    await emit(
        conn, name=name, tenant_id=a.tenant, aggregate_type="verification_session", aggregate_id=a.session.session_ref,
        aggregate_version=await applicant_session.next_event_version(conn, a.session.session_ref),
        data={"session_ref": a.session.session_ref, **data},
    )  # fmt: skip


def _status(s: Any, case: svc.Case | None) -> dict[str, Any]:
    return flow.applicant_view(s, case, datetime.now(UTC))


async def _case(conn: AsyncConnection, s: Any) -> svc.Case | None:
    return await svc.get(conn, str(s.case_id)) if s.case_id else None


def _require(state: str, allowed: frozenset[str] | set[str], what: str) -> None:
    if state == "EXPIRED":
        raise ZidError("SESSION_EXPIRED", ap.MESSAGES["SESSION_EXPIRED"][1], http_status=410)
    if state not in allowed:
        raise ZidError(
            "SESSION_STATE_CONFLICT", f"This cannot be done now ({what}).", http_status=409
        )


def _rules(request: Request, market: str) -> Any:
    try:
        return idv_cases._rules(request, market)
    except ZidError as e:
        raise _safe(e) from e


def _face_route_on(request: Request) -> bool:
    return getattr(request.app.state, "idv_face_route_ack", None) == idv_cases.FACE_ROUTE_ACK


async def hosted_gate(request: Request, conn: AsyncConnection, market: str, return_url: str | None) -> str:
    """What a hosted session must pass before it opens (wired into privacy by main): the market's country, pack, release, entitlement and kill-switch
    checks (WF-01 §04, Q04) and a return address on one of the business's verified domains (S10). Returns the mode the session runs in."""
    environment = environment_of(request)
    await enforce_regulated(request, market, Capability.INDIVIDUAL_VERIFICATION)
    if return_url and not applicant_session.return_url_allowed(return_url, await verified_domains(conn), local=environment in ("local", "test")):
        raise ZidError("RETURN_URL_NOT_ALLOWED", "The return address must be https on one of the organization's verified domains.", http_status=422)
    return "sandbox" if environment in SANDBOX_ENVIRONMENTS else "production"


# ------------------------------------------------------------------------------------------------------------------------------ launch


class LaunchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    launch_token: str = Field(min_length=40, max_length=200)


@router.post("/v2/verification-sessions/launch")
async def launch(body: LaunchIn, request: Request) -> dict[str, Any]:
    """Spend the one-use link (WF-01 Q02, Q37). A used, expired, revoked or forged link is refused; a suspended or closed organization starts nothing."""
    now = datetime.now(UTC)
    try:
        tenant = applicant_session.tenant_of(body.launch_token)
        async with tenant_transaction(request_engine(request), _ctx(tenant, None)) as conn:
            t = await load_tenant(conn, tenant)
            if t is None or t.status.value in ("SUSPENDED", "CLOSED"):
                raise ZidError("TOKEN_INVALID", "The link is not valid.", http_status=401)
            return await applicant_session.exchange(conn, tenant, body.launch_token, now)
    except PrivacyError as e:
        raise _privacy(e) from e


# ------------------------------------------------------------------------------------------------------------------------------ S01 / S02


@router.get("/v2/verification-sessions/{session_ref}/public-context")
async def public_context(
    a: ApplicantDep, request: Request, nfc: bool = False, camera: bool = True
) -> dict[str, Any]:
    """Who is asking and why, the notice, and the methods this person may use here: computed on the server for this market, its rules and this device."""
    s = a.session
    rules = _rules(request, s.market)
    methods, withheld = ap.eligible_methods(
        rules, nfc=nfc, camera=camera, face_route_on=_face_route_on(request)
    )

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        tenant = await load_tenant(conn, a.tenant)
        privacy = await session_status(conn, a.tenant, s.session_ref)
        if await applicant_session.mark_presented(conn, s.session_ref, datetime.now(UTC)):
            await _event(
                conn,
                a,
                "verification.session.notice_presented",
                {
                    "notice_version": s.notice_version,
                    "market_notice_version": rules.basis.notice_version,
                },
            )
        fresh = await applicant_session.session_row(conn, s.session_ref)
        return {
            "requester": {
                "name": tenant.display_name if tenant else None,
                "verified_by": "Zoiko iD",
            },
            "purpose_code": s.purpose_code,
            "market": s.market,
            "notice": privacy["notice"],
            "market_notice_version": rules.basis.notice_version,
            "lawful_basis": await applicant_session.lawful_basis(conn, s.activity_code),
            # only reviewed languages are offered (settings); machine translation of legal notices is never served
            "languages": list(getattr(request.app.state, "hosted_verify_languages", None) or ["en"]),
            "methods": [m.view() for m in methods],
            "methods_withheld": withheld,
            "alternatives": privacy["alternatives"],
            "status": _status(fresh, await _case(conn, fresh)),
        }

    return await _tx(request, a, go)


class NoticeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str = Field(pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")  # BCP 47
    consent: bool = False  # only where the activity's lawful basis is consent; another basis is never turned into consent (S01)
    biometric_consent: bool = (
        False  # a separate, unticked choice: declining stops only the face comparison (Q08, Q39)
    )


@router.post("/v2/verification-sessions/{session_ref}/notices")
async def notices(body: NoticeIn, a: ApplicantDep, request: Request) -> dict[str, Any]:
    """The person read the notice (its exact version and language are recorded before any evidence is collected, Q07)."""
    s = a.session
    now = datetime.now(UTC)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        case = await _case(conn, s)
        _require(
            flow.phase_of(s, case, now),
            {"CREATED", "NOTICE_PRESENTED", "METHOD_SELECTED"},
            "the notice",
        )
        if s.notice_version:
            await acknowledge_notice(conn, a.tenant, str(s.applicant_id), session_ref=s.session_ref, notice_version=s.notice_version,
                                     language=body.language, subject_ref=str(s.applicant_id))  # fmt: skip
        basis = await applicant_session.lawful_basis(conn, s.activity_code)
        if body.consent and basis == "CONSENT" and s.notice_version:
            await record_session_consent(conn, a.tenant, str(s.applicant_id), s.session_ref, notice_version=s.notice_version,
                                         capture_method="WEB", affirmative_action="hosted notice: agreed")  # fmt: skip
        await applicant_session.acknowledge(
            conn,
            s.session_ref,
            locale=body.language,
            biometric_consent=body.biometric_consent,
            now=now,
        )
        await _event(conn, a, "verification.session.notice_acknowledged", {
            "notice_version": s.notice_version, "locale": body.language, "lawful_basis": basis, "consent": body.consent and basis == "CONSENT",
            "face_comparison_agreed": body.biometric_consent,
        })  # fmt: skip
        fresh = await applicant_session.session_row(conn, s.session_ref)
        return _status(fresh, case)

    return await _tx(request, a, go)


class MethodIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: str = Field(min_length=1, max_length=60)
    nfc: bool = False
    camera: bool = True


@router.post("/v2/verification-sessions/{session_ref}/method-select")
async def method_select(body: MethodIn, a: ApplicantDep, request: Request) -> dict[str, Any]:
    """Only a method the server offers this person may be chosen; a tampered choice is refused (S02 Q05). Choosing it opens the proofing case, pinned to the
    market's rules, and records the notice and any face-comparison consent on it. Choosing the same method again changes nothing."""
    s = a.session
    rules = _rules(request, s.market)
    methods, _ = ap.eligible_methods(
        rules, nfc=body.nfc, camera=body.camera, face_route_on=_face_route_on(request)
    )
    chosen = next((m for m in methods if m.method == body.method), None)
    if chosen is None:
        raise _safe(
            ZidError("METHOD_NOT_ELIGIBLE", "That method is not available here.", http_status=422)
        )
    if s.acknowledged_at is None:
        raise ZidError(
            "NOTICE_NOT_ACKNOWLEDGED", "Read the notice before choosing a method.", http_status=409
        )
    if chosen.biometric and s.biometric_consent_at is None:
        # Q08/Q39: declining the face comparison stops only that route; the person chooses another
        raise ZidError(
            "CONSENT_OR_NOTICE_REQUIRED",
            "This way needs your agreement to a face comparison. Choose another way, or go back and agree.",
            http_status=409,
        )
    if s.case_id is not None:

        async def same(conn: AsyncConnection) -> dict[str, Any]:
            case = await _case(conn, s)
            if case is not None and case.route != body.method:
                raise ZidError(
                    "METHOD_ALREADY_SELECTED",
                    "A method was already chosen for this session.",
                    http_status=409,
                )
            return _status(s, case)

        return await _tx(request, a, same)
    try:
        opened = await idv_cases.open_case(
            idv_cases.OpenIn(market=s.market, route=body.method), request, a.caller
        )
    except ZidError as e:
        raise _safe(e) from e
    case_id = str(opened["case_id"])
    now = datetime.now(UTC)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await applicant_session.link_case(conn, s.session_ref, case_id)
        case = await svc.get(conn, case_id)
        assert case is not None
        if rules.basis.notice_version:
            case = await svc.record_notice_and_consent(conn, case_id, rules, notice_version=rules.basis.notice_version,
                                                       consented=s.biometric_consent_at is not None, now=now)  # fmt: skip
        await _event(conn, a, "verification.session.method_selected", {
            "method": body.method, "policy_version": case.policy_version, "policy_hash": case.policy_hash,
            "device_capability_flags": {"nfc": body.nfc, "camera": body.camera},
        })  # fmt: skip
        fresh = await applicant_session.session_row(conn, s.session_ref)
        return _status(fresh, case)

    return await _tx(request, a, go)


# ------------------------------------------------------------------------------------------------------------------------------ S04 to S07


class IntentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step: str = Field(pattern=r"^(DOCUMENT|FACE|CHIP)$")


@router.post("/v2/verification-sessions/{session_ref}/capture-intents", status_code=201)
async def capture_intent(body: IntentIn, a: ApplicantDep, request: Request) -> dict[str, Any]:
    """A one-time value bound to this session's case, its method and its market, valid for minutes; the capture must carry it (S05 capture integrity)."""
    s = a.session
    now = datetime.now(UTC)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        case = await _case(conn, s)
        _require(flow.phase_of(s, case, now), ap.TAKES_EVIDENCE, "a capture")
        try:
            value, c = await svc.issue_nonce(conn, str(a.case_id), now)
        except svc.CaseError as e:
            raise _safe(idv_cases._fail(e)) from e
        issued = c.nonce_issued_at or now
        await _event(conn, a, "verification.session.capture_started", {
            "step": body.step, "method": c.route, "capture_intent_id": hashlib.sha256(value.encode()).hexdigest()[:32], "region": s.market,
        })  # fmt: skip
        return {"nonce": value, "step": body.step, "method": c.route, "region": s.market, "issued_at": issued.isoformat(),
                "expires_at": (issued + svc.CAPTURE_INTENT_TTL).isoformat()}  # fmt: skip

    return await _tx(request, a, go)


async def _evidence(
    request: Request,
    a: Applicant,
    media: bytes | None,
    run: Callable[[], Awaitable[dict[str, Any]]],
    step: str,
) -> dict[str, Any]:
    """Evidence goes through the proofing route itself (every check, ceiling and gate unchanged), then the person sees only the safe status. Every
    attempt, accepted or not, is recorded with its number and a result code (WF-02 M06): the number comes from the case's own attempt counter, so a
    reload or a device switch never makes a new first attempt (Q06)."""
    now = datetime.now(UTC)
    before = 0

    async def check(conn: AsyncConnection) -> None:
        nonlocal before
        case = await _case(conn, a.session)
        _require(flow.phase_of(a.session, case, now), ap.TAKES_EVIDENCE, "evidence")
        before = sum(case.attempts.values()) if case else 0

    await _tx(request, a, check)
    try:
        await run()
    except ZidError as e:
        safe = _safe(e)

        async def failed(conn: AsyncConnection) -> None:  # its own transaction: the attempt is on record even though the request fails
            await _event(conn, a, "verification.session.capture_attempt_completed", {
                "attempt_no": before + 1, "step": step, "result": "TECHNICAL_FAILED" if safe.code == "PROVIDER_TEMPORARILY_UNAVAILABLE" else safe.code,
            })  # fmt: skip

        await _tx(request, a, failed)
        raise safe from e

    async def after(conn: AsyncConnection) -> dict[str, Any]:
        case = await _case(conn, a.session)
        assert case is not None
        await _event(conn, a, "verification.session.media_accepted", {
            "case_version": case.version, "media_digest": hashlib.sha256(media).hexdigest() if media else None,
            "source_attestation_result": {True: "RISK", False: "ESTABLISHED", None: "NOT_ESTABLISHED"}[case.signals.injection_risk],
        })  # fmt: skip
        told = ap.action(flow.phase_of(a.session, case, now), case.signals, submitted=False)
        await _event(conn, a, "verification.session.capture_attempt_completed", {
            "attempt_no": before + 1, "step": step,
            "result": {None: "ACCEPTED", "CAPTURE_QUALITY": "RETAKE_NEEDED", "SUSPICIOUS_SIGNAL": "RISK"}.get(told, told),
        })  # fmt: skip
        return _status(a.session, case)

    return await _tx(request, a, after)


def _bytes(value: str) -> bytes | None:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None


@router.post("/v2/verification-sessions/{session_ref}/evidence/document-image")
async def evidence_document(
    body: idv_cases.DocumentImageIn, a: ApplicantDep, request: Request
) -> dict[str, Any]:
    body = body.model_copy(
        update={"channel": "WEB"}
    )  # a hosted page is a browser capture: it can never be chip-verified
    return await _evidence(
        request,
        a,
        _bytes(body.image_base64),
        lambda: idv_cases.document_image(a.case_id, body, request, a.caller),
        "DOCUMENT",
    )


@router.post("/v2/verification-sessions/{session_ref}/evidence/chip")
async def evidence_chip(
    body: idv_cases.ChipIn, a: ApplicantDep, request: Request
) -> dict[str, Any]:
    return await _evidence(
        request, a, None, lambda: idv_cases.chip(a.case_id, body, request, a.caller), "CHIP"
    )


@router.post("/v2/verification-sessions/{session_ref}/evidence/nfc-state")
async def evidence_nfc_state(
    body: idv_cases.NfcStateIn, a: ApplicantDep, request: Request
) -> dict[str, Any]:
    return await _evidence(
        request, a, None, lambda: idv_cases.nfc_state(a.case_id, body, request, a.caller), "NFC_STATE"
    )


# ------------------------------------------------------------------------------------------------------------------------------ S08 to S12


@router.post("/v2/verification-sessions/{session_ref}/submit")
async def submit(a: ApplicantDep, request: Request) -> dict[str, Any]:
    """The one final submission (S08). It needs the notice acknowledged, capture still permitted and evidence on the case; a repeat returns the same state
    and records nothing again, so the business is told, and charged, once (Q33)."""
    s = a.session
    now = datetime.now(UTC)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        case = await _case(conn, s)
        if s.submitted_at is not None:
            return _status(s, case)
        _require(flow.phase_of(s, case, now), {"CAPTURING"}, "submitting before any evidence")
        assert case is not None
        privacy = await session_status(conn, a.tenant, s.session_ref)
        blockers = [b for b in privacy["blockers"] if b != "NON_BIOMETRIC_ALTERNATIVE_CHOSEN"]
        if blockers:
            raise ZidError(
                "CONSENT_OR_NOTICE_REQUIRED",
                "Something you need to agree to or read is still open.",
                http_status=409,
            )
        if await applicant_session.mark_submitted(conn, s.session_ref, now):
            await _event(conn, a, "verification.session.submitted", {"submission_id": s.session_ref, "case_version": case.version,
                                                                     "environment": s.environment})  # fmt: skip
            await flow.record_submission(conn, a.tenant, s.session_ref, now)
            await flow.finalize_if_due(conn, a.tenant, case, now, review_sla_hours=_rules(request, s.market).review_sla_hours)
        fresh = await applicant_session.session_row(conn, s.session_ref)
        return _status(fresh, case)

    return await _tx(request, a, go)


@router.get("/v2/verification-sessions/{session_ref}/public-status")
async def public_status(a: ApplicantDep, request: Request) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return _status(a.session, await _case(conn, a.session))

    return await _tx(request, a, go)


@router.post("/v2/verification-sessions/{session_ref}/handoff", status_code=201)
async def handoff(a: ApplicantDep, request: Request) -> dict[str, Any]:
    """A one-use link, valid for minutes, to continue on a phone (S06, S12; Q20). It carries no personal data; the phone spends it once for its own credential."""
    now = datetime.now(UTC)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        _require(
            flow.phase_of(a.session, await _case(conn, a.session), now),
            {"NOTICE_PRESENTED", *ap.TAKES_EVIDENCE},
            "a handoff",
        )
        launch = await applicant_session.issue_launch(
            conn, a.tenant, a.session.session_ref, now, handoff=True
        )
        base = str(getattr(request.app.state, "hosted_verify_base", "")).rstrip("/")
        return {
            "handoff_url": f"{base}/verify-session#t={launch['token']}",
            "expires_at": launch["expires_at"],
        }

    return await _tx(request, a, go)


@router.post("/v2/verification-sessions/{session_ref}/applicant-cancel")
async def applicant_cancel(a: ApplicantDep, request: Request) -> dict[str, Any]:
    if a.session.case_id is not None:
        try:
            await idv_cases.abandon(a.case_id, request, a.caller)
        except ZidError as e:
            if e.code != "CASE_CLOSED":
                raise _safe(e) from e

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await cancel_session(conn, a.tenant, a.session.session_ref)
        if a.session.state != "CANCELED":
            await _event(conn, a, "verification.session.canceled", {"by": "APPLICANT"})  # a terminal state on record (WF-02 section 05.1)
        fresh = await applicant_session.session_row(conn, a.session.session_ref)
        return _status(fresh, await _case(conn, fresh))

    return await _tx(request, a, go)


# ------------------------------------------------------------------------------------------------------------------------------ the business


@router.get("/v2/verification-sessions/{session_ref}/decision")
async def session_decision(session_ref: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    """The business's normalized result for its own session (tenant-scoped by row-level security; Q01). No image, document field or score."""
    idv_cases._scope(caller, "verification:read")
    now = datetime.now(UTC)
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            s = await applicant_session.session_row(conn, session_ref)
            case = await _case(conn, s)
            return {
                "session_ref": session_ref,
                "state": flow.phase_of(s, case, now),
                "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
                "mode": flow.applicant_view(s, case, now)["mode"],
                "decision": await flow.decision(conn, case) if case is not None else None,
            }
    except PrivacyError as e:
        raise _privacy(e) from e
