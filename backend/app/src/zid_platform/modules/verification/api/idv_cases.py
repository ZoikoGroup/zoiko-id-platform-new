"""Identity proofing cases (ZID-DTV-WF-03 sections 6, 10, 18; CR-110).

    POST /v1/idv/cases                          open a case for a market and route (CHIP, or an alternative the market's signed policy names)
    GET  /v1/idv/cases                          the company's cases: decision, reasons, signal states
    GET  /v1/idv/cases/{id}                     one case, with every decision it has reached
    POST /v1/idv/cases/{id}/nonce               a one-time value the next capture must carry
    POST /v1/idv/cases/{id}/nfc-state           the phone has no NFC, the person declined, or the read failed
    POST /v1/idv/cases/{id}/chip                what the chip returned, optionally with a live face and liveness capture; the platform judges the chip, and an approved engine judges the face
    POST /v1/idv/cases/{id}/document-image      a document photograph: the platform measures it, reads its printed lines and checks the capture's envelope and device report
    POST /v1/idv/cases/{id}/optical             the printed-document checks' results
    POST /v1/idv/cases/{id}/consent             the notice shown and, where it is the basis, consent
    POST /v1/idv/cases/{id}/consent:withdraw    withdraw it
    POST /v1/idv/cases/{id}/reviews             a reviewer signs an alternative-route case          (verification.idv.review)
    POST /v1/idv/cases/{id}:abandon             the applicant leaves

No route lets a caller state a result: signals come from the platform's own checks, and the decision is re-derived by the pinned rules. A market with no policy in `config/idv/market-policies.yaml` cannot open a case.
Responses carry codes and states only: no document data, no image. The face-match route's answer also carries the similarity and the threshold (two numbers) so the person, and the owner testing it, can see the comparison working."""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.evidence.ports import (
    LedgerError,
    chip_photo,
    effective_csca,
    scan_content,
    trust_config,
    verify_chip,
)
from zid_platform.modules.verification.application import idv_case as svc
from zid_platform.modules.verification.application import idv_claims, idv_media
from zid_platform.modules.verification.application.idv_document import read_document
from zid_platform.modules.verification.domain.idv_decision import ChipAuth, NfcAccess, assertions, face_signal
from zid_platform.modules.verification.domain.idv_evidence import Envelope
from zid_platform.modules.verification.domain.idv_policy import MarketRules, PolicyError
from zid_platform.modules.verification.infrastructure import (
    device_attestation,
    idv_policy_file,
    self_run_config,
)
from zid_platform.modules.verification.domain import document_details
from zid_platform.modules.verification.infrastructure.image_inspect import ImagingUnavailable, read_page_text
from zid_platform.modules.verification.infrastructure.image_inspect import (
    sanitize as sanitize_image,
)
from zid_platform.protected import protected_action

log = logging.getLogger(__name__)

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
    "FACE_ATTEMPTS_USED_UP": 429,
    "RETRY_CEILING_NOT_CONFIGURED": 409,
    "INVALID_ACCESS_STATE": 422,
    "MEDIA_RETENTION_NOT_SET": 409,
    "MEDIA_NOT_FOUND": 404,
    "MEDIA_EXPIRED": 410,
    "GRANT_INVALID": 403,
    "REASON_REQUIRED": 422,
    "ONLY_ON_REVIEWED_ROUTE": 409,
    "CODE_INVALID": 422,
    "DATE_ALREADY_ESTABLISHED": 409,
}


def _fail(e: svc.CaseError) -> ZidError:
    return ZidError(e.code, e.detail or e.code, http_status=_STATUS.get(e.code, 422))


def _scope(caller: Caller, scope: str) -> None:
    if scope not in caller.scope:
        raise ZidError("SCOPE_INSUFFICIENT", f"{scope} is required.", http_status=403)


def _global_rules(request: Request) -> MarketRules | None:
    """The built-in global policy, for a country with no policy of its own. Only with the owners' written acceptance configured; otherwise None, as before."""
    if getattr(request.app.state, "idv_global_policy_ack", None) != idv_policy_file.GLOBAL_POLICY_ACK:
        return None
    source = getattr(request.app.state, "idv_global_policy", None)  # tests hand in their own
    return source() if callable(source) else idv_policy_file.load_global()


def _rules(request: Request, market: str) -> MarketRules:
    source = getattr(request.app.state, "idv_policies", None)
    try:
        table = source() if callable(source) else idv_policy_file.load()
        rules = table.get(market.upper())
        if rules is None:
            rules = _global_rules(request)  # a country's own policy always wins; the global one only fills the gap
    except PolicyError as e:
        raise ZidError("IDV_POLICY_FILE_INVALID", str(e), http_status=503, retryable=True) from e
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


def view(c: svc.Case, notice: str | None = None) -> dict[str, Any]:
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
        "notice_version": notice,
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
    face: FaceIn | None = None


class AttestationIn(BaseModel):
    """The platform's own device report for this capture: for iOS App Attest, `attestation` and `key_id` in base64. It is checked here; the client never states that it passed."""

    model_config = ConfigDict(extra="forbid")
    platform: Literal["IOS_APP_ATTEST", "ANDROID_PLAY_INTEGRITY"]
    report: dict[str, Annotated[str, Field(max_length=64 * 1024)]] = Field(max_length=4)


class CaptureFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nonce: str = Field(min_length=8, max_length=128)
    captured_at: datetime
    virtual_camera: bool | None = None
    attestation: AttestationIn | None = None


class FaceIn(CaptureFacts):
    """A live face image and a liveness capture, each base64, with the facts of the capture (a fresh nonce from this case). Used only when an engine is approved for the market and the chip's portrait came from a data group
    the signed object covered; never kept."""

    probe_base64: str = Field(min_length=8, max_length=(2 * 1024 * 1024 * 4) // 3 + 16)
    liveness_base64: str = Field(min_length=8, max_length=(8 * 1024 * 1024 * 4) // 3 + 16)


class DeclaredIn(BaseModel):
    """What the applicant types, compared once with the platform's own reading of the document on an automatic route, and never stored."""

    model_config = ConfigDict(extra="forbid")
    surname: str = Field(min_length=1, max_length=80)
    given_names: str = Field(min_length=1, max_length=120)
    document_number: str = Field(min_length=3, max_length=20)
    birth_date: date


class DetailsIn(BaseModel):
    """The face-match route's optional details: typed by the person, compared once with the platform's own reading of the document, never stored."""

    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=120)
    number: str | None = Field(default=None, max_length=40)
    birth_date: date | None = None


class DocumentImageIn(CaptureFacts):
    image_base64: str = Field(min_length=8, max_length=(8 * 1024 * 1024 * 4) // 3 + 16)
    channel: Literal["APP", "WEB"] = "APP"
    declared: DeclaredIn | None = None
    # the face-match route (CR-129) only: a live photograph taken now, and the person's agreement to the comparison. Never kept.
    selfie_base64: str | None = Field(default=None, min_length=8, max_length=(8 * 1024 * 1024 * 4) // 3 + 16)
    consent: bool = False
    details: DetailsIn | None = None


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
    return view(c, _notice(request, c.market))


@router.get("/v1/idv/cases")
async def list_cases(request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:read")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        items = await svc.list_cases(conn)
    return {"items": [view(c, _notice(request, c.market)) for c in items]}


@router.get("/v1/idv/cases/{case_id}")
async def read_case(case_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:read")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        c = await svc.get(conn, str(case_id))
        if c is None:
            raise ZidError("CASE_NOT_FOUND", "No such case.", http_status=404)
        hist = await svc.history(conn, str(case_id))
    return {
        **view(c, _notice(request, c.market)),
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
    return view(c, _notice(request, c.market))


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


def _envelope(facts: CaptureFacts, media: bytes) -> Envelope:
    """The envelope the platform checks: the digest is computed here from the bytes, and the device report is verified here. Nothing the client says about either is believed."""
    digest = hashlib.sha256(media).hexdigest()
    attested = None
    if facts.attestation is not None:
        attested = device_attestation.verify(
            facts.attestation.platform,
            dict(facts.attestation.report),
            challenge=f"{facts.nonce}:{digest}".encode(),
        )
    return Envelope(facts.nonce, digest, facts.captured_at, facts.virtual_camera, attested)


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
    verified = verdict.detail.get("data_groups_verified")
    covered = {int(g) for g in verified} if isinstance(verified, list | tuple) else set()
    photo = (
        chip_photo(groups[2])
        if (verdict.status == "VERIFIED_LIMITED" and 2 in covered and 2 in groups)
        else None
    )
    notes: list[str] = []

    async def go(conn: Any, rules: MarketRules) -> svc.Case:
        c = await svc.submit_chip(
            conn, str(case_id), rules, status=verdict.status, reasons=verdict.reasons, document=doc,
            chip_auth=ChipAuth.NOT_TESTED, now=datetime.now(UTC),
        )  # fmt: skip
        if body.face is None:
            return c
        probe, media = _b64(body.face.probe_base64), _b64(body.face.liveness_base64)
        c = await svc.submit_capture(
            conn, str(case_id), rules, _envelope(body.face, probe), now=datetime.now(UTC)
        )
        try:
            return await svc.apply_engine(
                conn, str(case_id), rules, engine=getattr(request.app.state, "biometric_engine", None),
                gate=self_run_config.accepted_engine(), probe=probe, media=media, reference=photo, now=datetime.now(UTC),
            )  # fmt: skip
        except svc.CaseError as e:  # the chip result stands; the person signals simply did not run, and the reasons say why
            notes.append(e.code)
            return c

    out = await _mutate(request, caller, case_id, go)
    if notes:
        out["face_not_run"] = notes[0]
    return out


FACE_ROUTE_ACK = "accept-no-liveness-no-evaluation"


async def _face_against_document(request: Request, body: DocumentImageIn, selfie: bytes | None, document: bytes, threshold: float) -> tuple[Any, float | None, str | None]:
    """The face-match route's comparison (CR-129): the live photograph against the document's own photograph, in memory, for this one call. It needs the owner's acknowledgement, the person's
    agreement and a live photograph; the live photograph is scanned and re-encoded like the document, and neither image is kept. A face that cannot be found is a retake, never a failure."""
    from zid_platform.modules.verification.infrastructure import face_lab

    if getattr(request.app.state, "idv_face_route_ack", None) != FACE_ROUTE_ACK:
        raise ZidError("FACE_ROUTE_NOT_ENABLED", "Verification with a face match is not enabled in this deployment.", http_status=403)
    if not body.consent:
        raise ZidError("FACE_CONSENT_REQUIRED", "Agree to the comparison of your photographs first.", http_status=422)
    if selfie is None:
        raise ZidError("LIVE_PHOTO_REQUIRED", "A live photograph is needed with the document photograph.", http_status=422)
    verdict = await scan_content(getattr(request.app.state, "evidence_scanner", None), selfie)
    if verdict.state == "INFECTED":
        raise ZidError("IMAGE_REFUSED", "That file could not be accepted.", http_status=422)
    if verdict.state != "CLEAN":
        raise ZidError("SCAN_UNAVAILABLE", "The file could not be checked right now. Try again soon.", http_status=503, retryable=True)
    try:
        clean_selfie = await sanitize_image(selfie, transport=getattr(request.app.state, "imaging_transport", None))
    except ImagingUnavailable as e:
        raise ZidError(e.code, "The live photograph could not be inspected right now.", http_status=503, retryable=True) from e
    try:
        got = await face_lab.compare(clean_selfie, document)
    except face_lab.FaceLabError as e:
        if e.http_status == 422:
            log.info("idv face comparison: no result (%s)", e.code)  # the sidecar's reason only: which photograph, no image
            return face_signal(None, threshold), None, e.code  # no face, more than one face, unreadable: take it again
        raise ZidError("FACE_ENGINE_UNAVAILABLE", "The face comparison is not available right now. Try again soon.", http_status=503, retryable=True) from e
    cosine = float(got["cosine"])
    log.info("idv face comparison: similarity %.3f, threshold %.3f", cosine, threshold)  # the number only, so a person can see whether the comparison works; no photograph, no name
    return face_signal(cosine, threshold), cosine, None


@router.post("/v1/idv/cases/{case_id}/document-image")
async def document_image(
    case_id: uuid.UUID, body: DocumentImageIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """A document photograph. The platform computes the digest, inspects the image through the imaging sidecar, reads the printed machine-readable lines itself and verifies any device report: the client states none of it."""
    image = _b64(body.image_base64)
    # what arrives is scanned for malware first, then re-encoded by the sidecar as a plain JPEG with its metadata (location, device, time) removed. The checks, the printed-line reading and anything kept all use the clean
    # copy; only the replay digest is taken from what was received. A deployed cell with no scanner refuses rather than pretend to scan.
    selfie = _b64(body.selfie_base64) if body.selfie_base64 else None
    verdict = await scan_content(getattr(request.app.state, "evidence_scanner", None), image)
    if verdict.state == "INFECTED":
        raise ZidError("IMAGE_REFUSED", "That file could not be accepted.", http_status=422)
    if verdict.state != "CLEAN":
        raise ZidError(
            "SCAN_UNAVAILABLE",
            "The file could not be checked right now. Try again soon.",
            http_status=503,
            retryable=True,
        )
    try:
        transport = getattr(request.app.state, "imaging_transport", None)
        clean = await sanitize_image(image, transport=transport)
        reading = await read_document(clean, transport=transport)
    except ImagingUnavailable as e:
        raise ZidError(
            e.code,
            "The document image could not be inspected right now.",
            http_status=503,
            retryable=True,
        ) from e
    env = _envelope(body, image)
    now = datetime.now(UTC)
    tenant = str(caller.ctx.tenant_id or "")

    shown: dict[str, Any] = {}  # the similarity and the threshold, shown to the person on this answer only: a number, so the comparison can be seen working

    async def work(conn: Any, rules: MarketRules) -> svc.Case:
        before = await svc.get(conn, str(case_id))
        face_route = next((a for a in rules.policy.alternates if before is not None and a.route_id == before.route and a.face_match), None)
        face = None
        checks = dict(reading.checks)
        shown.clear()
        if face_route is not None:
            if body.details is not None:
                try:
                    text = await read_page_text(clean, transport=getattr(request.app.state, "imaging_transport", None))
                except ImagingUnavailable:
                    text = ""  # nothing could be read: every typed detail is then "not read", held against nobody
                found = document_details.compare(text, name=body.details.name, number=body.details.number, birth_date=body.details.birth_date)
                wrong = document_details.contradicted(found)
                if wrong:
                    # refused before anything is decided or spent: a typing slip is corrected and sent again on the same case
                    raise ZidError("DETAILS_DO_NOT_MATCH_DOCUMENT", "These details are not on the document photograph: " + ", ".join(w.replace("_", " ") for w in wrong) + ". Correct them or leave them empty, then send again.", http_status=422)
                shown["details"] = found
            face, cosine, problem = await _face_against_document(request, body, selfie, clean, face_route.face_threshold)
            shown.update(face_similarity=None if cosine is None else round(cosine, 3), face_threshold=face_route.face_threshold, face_problem=problem)
            checks = {k: v for k, v in checks.items() if not k.startswith("MRZ_")}  # this route reads no machine-readable lines: their absence is not a doubt about the picture
        automatic = before is not None and any(
            a.route_id == before.route and a.automatic for a in rules.policy.alternates
        )
        keep = (
            before is not None and before.route != "CHIP" and not automatic
        )  # a case that people decide keeps its photograph so they can see it; one the platform decides keeps nothing
        if keep and not rules.media_retention_days:
            raise svc.CaseError(
                "MEDIA_RETENTION_NOT_SET",
                "This way of verifying keeps a photograph for review, and no retention period is set for this market.",
            )
        kind = idv_media.sniff(clean)
        if keep and kind is None:
            raise svc.CaseError("CODE_INVALID", "The photograph must be a JPEG or a PNG.")
        out = await svc.submit_document(
            conn,
            str(case_id),
            rules,
            env,
            checks=checks,
            printed=reading.printed,
            now=now,
            web=body.channel == "WEB",
            declared=(
                {**body.declared.model_dump(exclude={"birth_date"}), "birth_date": body.declared.birth_date.isoformat()}
                if body.declared
                else None
            ),
            face=face,
        )
        if keep and kind is not None:
            await idv_media.save(
                conn,
                tenant,
                out,
                clean,
                content_type=kind,
                checks=dict(reading.checks),
                captured_at=body.captured_at,
                retention_days=rules.media_retention_days,
                now=now,
            )
        return out

    result = await _mutate(request, caller, case_id, work)
    return {**result, **shown} if shown else result


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
    return view(c, _notice(request, c.market))


def _notice(request: Request, market: str) -> str | None:
    """The notice version the market's rules say applicants must be shown, or None when there is none (so nothing can be consented to). Never raises: a missing or broken file is simply no notice."""
    try:
        source = getattr(request.app.state, "idv_policies", None)
        table = source() if callable(source) else idv_policy_file.load()
        rules = table.get(market.upper()) or _global_rules(request)
    except PolicyError:
        return None
    return rules.basis.notice_version if rules else None


# ---------------------------------------------------------------------------------------------------- the reviewer's side of a reviewed route (CR-124)


class ReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=10, max_length=500)


class CodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^[A-Z][A-Z_]{2,40}$")


@router.get("/v1/idv/cases/{case_id}/review")
async def review_view(case_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    """What a reviewer sees of a case before opening anything: the decision and reasons, who signed, and that a photograph exists, when it was taken, until when it is kept and what was measured. Never the image."""
    _scope(caller, "verification:read")
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        c = await svc.get(conn, str(case_id))
        if c is None:
            raise ZidError("CASE_NOT_FOUND", "No such case.", http_status=404)
        photo = await idv_media.describe(conn, str(case_id), now=now)
        claim = await idv_claims.for_case(conn, str(case_id))
    return {
        **view(c, _notice(request, c.market)),
        "photo": photo,
        "reject_codes": sorted(svc.REJECT_CODES),
        "recapture_codes": sorted(svc.RECAPTURE_CODES),
        "document_date_known": c.signals.document_expired is not None,
        "claim": claim,
    }


@router.post("/v1/idv/cases/{case_id}/media:access", status_code=201)
async def media_access(
    case_id: uuid.UUID, body: ReasonIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """Ask to open the photograph: a protected act with a reason, answered with a grant that lasts minutes and reads at most three times."""
    _scope(caller, "verification:execute")
    me, tenant = str(caller.ctx.principal_id), str(caller.ctx.tenant_id or "")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="evidence.object.read", resource=f"idv_case:{case_id}"
        )
        try:
            return await idv_media.open_grant(
                conn, tenant, str(case_id), reviewer=me, reason=body.reason, now=datetime.now(UTC)
            )
        except svc.CaseError as e:
            raise _fail(e) from e


@router.get("/v1/idv/cases/{case_id}/media/{grant_id}")
async def media_read(
    case_id: uuid.UUID, grant_id: uuid.UUID, request: Request, caller: CallerDep
) -> JSONResponse:
    _scope(caller, "verification:execute")
    me, tenant = str(caller.ctx.principal_id), str(caller.ctx.tenant_id or "")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            got = await idv_media.read_with_grant(
                conn, tenant, str(case_id), str(grant_id), reviewer=me, now=datetime.now(UTC)
            )
        except svc.CaseError as e:
            raise _fail(e) from e
        except LedgerError as e:
            raise ZidError(
                "MEDIA_NOT_AVAILABLE", "The photograph is no longer held.", http_status=410
            ) from e
    body = {
        "content_type": got["content_type"],
        "image_base64": base64.b64encode(got["content"]).decode("ascii"),
        "checks": got["checks"],
        "reads_left": got["reads_left"],
    }
    return JSONResponse(body, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


async def _reviewer_decision(
    request: Request, caller: Caller, case_id: uuid.UUID, fn: Any
) -> dict[str, Any]:
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
            c = await fn(conn, rules, me, datetime.now(UTC))
        except svc.CaseError as e:
            raise _fail(e) from e
    return view(c, _notice(request, c.market))


@router.post("/v1/idv/cases/{case_id}/reject")
async def reject(
    case_id: uuid.UUID, body: CodeIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """A reviewer rejects a reviewed-route case for one defined failure."""
    return await _reviewer_decision(
        request,
        caller,
        case_id,
        lambda conn, rules, me, now: svc.reject_case(
            conn, str(case_id), rules, reviewer=me, code=body.code, now=now
        ),
    )


@router.post("/v1/idv/cases/{case_id}/recapture")
async def recapture(
    case_id: uuid.UUID, body: CodeIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """A reviewer asks the applicant for a clearer photograph. The old one can no longer be opened."""

    async def act(conn: Any, rules: MarketRules, me: str, now: datetime) -> svc.Case:
        c = await svc.request_recapture(
            conn, str(case_id), rules, reviewer=me, code=body.code, now=now
        )
        await idv_media.supersede(conn, str(case_id), now)
        return c

    return await _reviewer_decision(request, caller, case_id, act)


class DateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expired: bool


@router.post("/v1/idv/cases/{case_id}/attest-date")
async def attest_date(
    case_id: uuid.UUID, body: DateIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """A reviewer says whether the document is in date, when the platform's own reading could not (no machine-readable lines, or lines that did not read reliably). It is a logged reviewer act, recorded as an attestation."""
    return await _reviewer_decision(
        request,
        caller,
        case_id,
        lambda conn, rules, me, now: svc.attest_document_date(
            conn, str(case_id), rules, reviewer=me, expired=body.expired, now=now
        ),
    )


@router.get("/v1/idv/review-queue")
async def review_queue(request: Request, caller: CallerDep) -> dict[str, Any]:
    """The cases waiting for reviewers in this company, oldest first, for a person who may review them: never their own case, and not one they already signed. Each says how long it has waited and, where the market sets a
    deadline, when it falls due and whether it is overdue."""
    _scope(caller, "verification:read")
    me = str(caller.ctx.principal_id)
    now = datetime.now(UTC)
    deadlines: dict[str, int | None] = {}
    items: list[dict[str, Any]] = []
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        for c in sorted(await svc.list_cases(conn, limit=200), key=lambda x: x.created_at):
            if (
                c.state != "OPEN"
                or c.route == "CHIP"
                or c.decision.value != "REVIEW_REQUIRED"
                or c.applicant == me
                or me in c.signals.reviewers
            ):
                continue
            if c.market not in deadlines:
                try:
                    deadlines[c.market] = _rules(request, c.market).review_sla_hours
                except ZidError:
                    deadlines[c.market] = None
            sla = deadlines[c.market]
            due = c.created_at + timedelta(hours=sla) if sla else None
            photo = await idv_media.describe(conn, c.case_id, now=now)
            items.append(
                {
                    "case_id": c.case_id, "market": c.market, "route": c.route, "created_at": c.created_at.isoformat(), "waiting_hours": int((now - c.created_at).total_seconds() // 3600),
                    "due_at": due.isoformat() if due else None, "overdue": bool(due and now > due), "signatures": len(c.signals.reviewers), "has_photo": photo["has_photo"],
                }
            )  # fmt: skip
    return {"items": items, "overdue": sum(1 for i in items if i["overdue"])}
