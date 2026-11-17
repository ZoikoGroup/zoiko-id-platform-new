"""Self-run identity verification over HTTP (CR-095, CR-102): a verification session that no external provider handles.

    POST /v2/self-run/verifications                       start a session that waits for the person's chip evidence
    POST /v2/self-run/verifications/{id}/chip             send the chip's signed object and data groups; the platform judges them

The start goes through the same gates as a provider session (country, policy pack, privacy, processing purpose). The chip is passively authenticated
(the signed object, the chain to a country signing CA the operator configured, every data group against its signed hash) and judged by the profile
exactly as a provider's result is. A chip alone proves the DOCUMENT is genuine, not that the person holding it is its holder: a profile that needs
binding goes to review, and no face or liveness engine is accepted in this deployment unless the engine gate says so. With no country signing CA
configured the chip cannot be judged and the session keeps waiting; it is never approved. Nothing here stores the chip or any photo.

The routes answer 404 unless `ZID_SELF_RUN_ENABLED` is set.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.evidence.ports import (
    chip_photo,
    effective_csca,
    trust_config,
    verify_chip,
)
from zid_platform.modules.verification.api.verification import (
    StartIn,
    _country_gate,
    _err,
    _pack_gate,
    _privacy_gate,
    _record_basis,
    _scope,
    _session_view,
)
from zid_platform.modules.verification.application import biometric_eligibility, chip_link, service
from zid_platform.modules.verification.application.identity_signal_feed import observe_request
from zid_platform.modules.verification.domain.biometric_methods import Method
from zid_platform.modules.verification.domain.self_run import ChipEvidence
from zid_platform.modules.verification.domain.verification import (
    SessionStatus,
    VerificationError,
)
from zid_platform.modules.verification.infrastructure import verification_store as store
from zid_platform.modules.verification.infrastructure.country_registry import registry
from zid_platform.modules.verification.infrastructure.privacy_overlays import privacy_overlays

CallerDep = Annotated[Caller, Depends(authenticated_caller)]


async def _on(request: Request) -> None:
    if not getattr(request.app.state, "self_run_enabled", False):
        raise ZidError(
            "SELF_RUN_NOT_ENABLED",
            "Self-run identity verification is not switched on for this environment.",
            http_status=404,
        )


router = APIRouter(tags=["verification-v2"], dependencies=[Depends(_on)])


class ActiveAuthIn(BaseModel):
    """The chip's signed answer to the challenge the session issued (`aa_challenge`), as base64 of the plain r||s signature. DG15 must be among the
    data groups sent."""

    model_config = ConfigDict(extra="forbid")
    response_base64: str = Field(min_length=8, max_length=2048)
    hash_name: Literal["SHA-256", "SHA-384", "SHA-512"] | None = None


class FaceIn(BaseModel):
    """A live face image and a liveness capture, each base64. Used only when the deployment has an accepted engine and the chip photo came from a data
    group the signed object covered; never kept."""

    model_config = ConfigDict(extra="forbid")
    probe_base64: str = Field(min_length=8, max_length=(2 * 1024 * 1024 * 4) // 3 + 16)
    liveness_base64: str = Field(min_length=8, max_length=(8 * 1024 * 1024 * 4) // 3 + 16)


class SelfRunChipIn(BaseModel):
    """What the phone app read from the chip: the signed object (EF.SOD) and the data groups, each as base64."""

    model_config = ConfigDict(extra="forbid")
    sod_base64: str = Field(min_length=8, max_length=(64 * 1024 * 4) // 3 + 16)
    data_groups: dict[
        Annotated[str, Field(pattern=r"^([1-9]|1[0-6])$")],
        Annotated[str, Field(min_length=4, max_length=(256 * 1024 * 4) // 3 + 16)],
    ] = Field(min_length=1, max_length=16)
    active_authentication: ActiveAuthIn | None = None
    face: FaceIn | None = None


def _b64(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ZidError(
            "VALIDATION_FAILED", "The chip data is not valid base64.", http_status=422
        ) from e


@router.post("/v2/self-run/verifications", status_code=201)
async def start(body: StartIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:execute")
    await observe_request(
        request_engine(request),
        caller.ctx,
        str(body.subject_principal_id),
        request,
        datetime.now(UTC),
    )
    _country_gate(request, body.jurisdiction)
    basis = await _pack_gate(request, caller, body.jurisdiction)
    privacy = await _privacy_gate(
        request,
        caller,
        body.jurisdiction,
        str(body.profile_id),
        str(body.subject_principal_id),
        purpose=body.purpose,
        self_run=True,  # the self-run engine is checked at the chip and face stage (CR-107)
    )
    try:
        s, challenge = await service.start_self_run(
            request_engine(request),
            ctx=caller.ctx,
            profile_id=str(body.profile_id),
            subject_principal_id=str(body.subject_principal_id),
            purpose=body.purpose,
            jurisdiction=body.jurisdiction,
            floor=body.required_level,
            now=datetime.now(UTC),
            country_registry_version=registry().version,
        )
    except VerificationError as e:
        raise _err(e) from e
    await _record_basis(request, caller, s.session_id, basis, privacy)
    return {
        **_session_view(s),
        "method": "SELF_RUN",
        "awaiting": "CHIP_EVIDENCE",
        "aa_challenge": challenge,  # 8 bytes in hex for the chip to sign (Active Authentication); a new one comes back after each attempt that leaves the session waiting
    }


@router.post("/v2/self-run/verifications/{verification_id}/chip")
async def submit_chip(
    verification_id: uuid.UUID, body: SelfRunChipIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    _scope(caller, "verification:execute")
    sod = _b64(body.sod_base64)
    groups = {int(k): _b64(v) for k, v in body.data_groups.items()}
    aa = body.active_authentication
    return await _judge_chip(
        request,
        caller,
        verification_id,
        sod,
        groups,
        aa_response=_b64(aa.response_base64) if aa is not None else None,
        aa_hash=aa.hash_name if aa is not None else None,
        face=body.face,
    )


async def _judge_chip(
    request: Request,
    caller: Caller,
    verification_id: uuid.UUID,
    sod: bytes,
    groups: dict[int, bytes],
    *,
    aa_response: bytes | None,
    aa_hash: str | None,
    face: FaceIn | None,
) -> dict[str, Any]:
    """Judge one chip's evidence for a waiting self-run session. The same judgement whether the app sent the evidence (the chip route) or our own reader
    got it off the chip (the relay): passive authentication against the trust the deployment or the governed store holds, then the profile decides."""
    config = getattr(request.app.state, "trust_config", None) or trust_config()
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        effective = await effective_csca(
            conn, config.csca
        )  # the governed trust store once it has been used, else the config folder (CR-107)
    verdict = verify_chip(sod, groups, csca=effective.csca, crls=config.crls)
    chip = ChipEvidence.from_verdict(verdict.status, verdict.reasons, verdict.detail)
    verified = verdict.detail.get("data_groups_verified")
    covered = {int(g) for g in verified} if isinstance(verified, list | tuple) else set()
    active = None
    if aa_response is not None:
        active = service.ActiveAuthInput(
            response=aa_response,
            dg15=groups.get(15, b""),
            dg15_verified=15 in covered and 15 in groups,
            hash_name=aa_hash,
        )
    biometric = None
    engine = getattr(request.app.state, "biometric_engine", None)
    photo = (
        chip_photo(groups[2]) if (2 in covered and 2 in groups and chip.photo_verified) else None
    )
    allowed: dict[str, Any] | None = None
    if face is not None and engine is not None and photo is not None:
        # CR-107: an accepted engine is not enough. Face match AND liveness must both be approved for this exact scope, with this tenant's live
        # DPIA and the country overlay; otherwise the face is not scored and the session is judged on the chip alone (never a pass by default).
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            pending = await store.load_session(conn, str(verification_id))
            checked = await biometric_eligibility.eligibility(
                conn,
                tenant_id=caller.ctx.tenant_id or "",
                country=pending.jurisdiction,
                purpose=pending.purpose.upper(),
                rollout_key=pending.subject_principal_id,  # CR-110: a partial rollout picks people
                overlays=getattr(request.app.state, "privacy_overlays", None) or privacy_overlays(),
                now=datetime.now(UTC),
            )
        allowed = checked.view()
        if not (checked.available(Method.FACE_MATCH) and checked.available(Method.LIVENESS)):
            async with tenant_transaction(request_engine(request), caller.ctx) as conn:
                await biometric_eligibility.record_blocked(
                    conn, caller.ctx.tenant_id or "", checked, route="self-run"
                )
        if checked.available(Method.FACE_MATCH) and checked.available(Method.LIVENESS):
            biometric = service.BiometricInput(
                engine, _b64(face.probe_base64), _b64(face.liveness_base64), photo
            )
    try:
        done = await service.submit_self_run(
            request_engine(request),
            ctx=caller.ctx,
            session_id=str(verification_id),
            chip=chip,
            now=datetime.now(UTC),
            country_check=lambda c: _country_gate(request, c),
            active_auth=active,
            biometric=biometric,
        )
    except VerificationError as e:
        raise _err(e) from e
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        s = await store.load_session(conn, str(verification_id))
    return {
        **_session_view(s),
        "method": "SELF_RUN",
        "status": done.status,
        "outcome": done.outcome,
        "reason_codes": list(done.codes),
        "aa_challenge": done.next_challenge,
        "chip_status": verdict.status,
        "holder_bound": False,
        "biometric_eligibility": allowed,  # null when no face was sent or no engine is configured
        "limitations": [
            "A chip proves the document is genuine, not that the person presenting it is its holder.",
            "A cloned chip is detected only when the Active Authentication answer is sent and verifies; otherwise it is not checked.",
            "Nothing from the chip was stored.",
        ],
    }


# --------------------------------------------------------------------------------------------------------------------- the chip relay (CR-108)


class ChipLinkStartIn(BaseModel):
    """The printed details the chip's access keys come from: the three machine-readable-zone details, or only the Card Access Number (CAN) printed on the document, which
    works for a chip that offers PACE. They are used to open the chip and kept nowhere."""

    model_config = ConfigDict(extra="forbid")
    document_number: str | None = Field(default=None, pattern=r"^[A-Za-z0-9<]{1,9}$")
    date_of_birth: str | None = Field(default=None, pattern=r"^\d{6}$")
    document_valid_to: str | None = Field(
        default=None, pattern=r"^\d{6}$"
    )  # the document's printed end date, YYMMDD
    can: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{4,16}$")
    include_photo: bool = False  # the photo group is read only when a face check will use it

    @model_validator(mode="after")
    def _one_way_in(self) -> ChipLinkStartIn:
        details = (self.document_number, self.date_of_birth, self.document_valid_to)
        if any(d is None for d in details) and (
            any(d is not None for d in details) or self.can is None
        ):
            raise ValueError("give all three document details, or only the Card Access Number")
        return self


class ChipLinkStepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=16, max_length=360_000)
    response_base64: str = Field(min_length=4, max_length=(chip_link.MAX_RESPONSE * 4) // 3 + 16)


class ChipLinkFinishIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=16, max_length=360_000)
    face: FaceIn | None = None


_LINK_STATUS = {
    "CHIP_LINK_NOT_FOUND": 404,
    "CHIP_LINK_NOT_WAITING": 409,
    "CHIP_LINK_NOT_FINISHED": 409,
    "CHIP_LINK_TIMEOUT": 408,
    "CHIP_LINK_TOO_LARGE": 413,
    "CHIP_RESPONSE_INVALID": 422,
    "TOO_MANY_COMMANDS": 422,
    "READER_ERROR": 500,
}


def _owner(caller: Caller) -> chip_link.Owner:
    return chip_link.Owner(
        caller.ctx.tenant_id or "", str(caller.ctx.principal_id or ""), caller.client_id
    )


def _link_error(e: chip_link.LinkError) -> ZidError:
    return ZidError(
        e.code,
        (e.detail or e.code.replace("_", " ").capitalize()) + ".",
        http_status=_LINK_STATUS.get(e.code, 422),
    )


def _step_view(step: chip_link.Step) -> dict[str, Any]:
    if step.kind == "apdu":
        return {
            "state": "READING",
            "apdu_base64": base64.b64encode(step.apdu).decode(),
            "token": step.token,
            "expires_in": step.expires_in,
        }
    return {
        "state": "READ",
        "token": step.token,
        "expires_in": step.expires_in,
        **(step.summary or {}),
        "next": "finish judges what was read; nothing has been judged yet",
    }


async def _waiting_session(request: Request, caller: Caller, verification_id: uuid.UUID) -> Any:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            s = await store.load_session(conn, str(verification_id))
        except VerificationError as e:
            raise _err(e) from e
    if s.adapter_id != service.SELF_RUN_ADAPTER or s.status is not SessionStatus.IN_PROGRESS:
        raise _err(VerificationError("SESSION_NOT_AWAITING_EVIDENCE"))
    return s


@router.post("/v2/self-run/verifications/{verification_id}/chip-link", status_code=201)
async def chip_link_start(
    verification_id: uuid.UUID, body: ChipLinkStartIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """Open a chip read. The answer is the first command for the device to send to the chip, and a sealed token to send back with the chip's answer.
    The server keeps nothing: every step carries its state in the token, so any server process can take it."""
    _scope(caller, "verification:execute")
    s = await _waiting_session(request, caller, verification_id)
    challenge = (s.pending_result or {}).get("aa_challenge")
    try:
        step = await chip_link.start(
            _owner(caller),
            s.session_id,
            document_number=body.document_number or "",
            date_of_birth=body.date_of_birth or "",
            document_valid_to=body.document_valid_to or "",
            can=body.can,
            challenge_hex=challenge if isinstance(challenge, str) else None,
            include_photo=body.include_photo,
        )
    except chip_link.LinkError as e:
        raise _link_error(e) from e
    return _step_view(step)


@router.post("/v2/self-run/verifications/{verification_id}/chip-link/step")
async def chip_link_step(
    verification_id: uuid.UUID, body: ChipLinkStepIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """The chip's answer to the last command, with the token that came with it; the reply is the next command, or the end of the read."""
    _scope(caller, "verification:execute")
    try:
        step = await chip_link.step(
            _owner(caller), str(verification_id), body.token, _b64(body.response_base64)
        )
    except chip_link.LinkError as e:
        raise _link_error(e) from e
    return _step_view(step)


@router.post("/v2/self-run/verifications/{verification_id}/chip-link/finish")
async def chip_link_finish(
    verification_id: uuid.UUID, body: ChipLinkFinishIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """Judge what the chip gave, exactly as if the app had sent it, with an optional face for an engine the deployment accepts. The session is decided once."""
    _scope(caller, "verification:execute")
    try:
        read = await chip_link.take_result(_owner(caller), str(verification_id), body.token)
    except chip_link.LinkError as e:
        raise _link_error(e) from e
    return await _judge_chip(
        request,
        caller,
        verification_id,
        read.chip.sod,
        read.chip.data_groups,
        aa_response=read.active_authentication,
        aa_hash=None,
        face=body.face,
    )
