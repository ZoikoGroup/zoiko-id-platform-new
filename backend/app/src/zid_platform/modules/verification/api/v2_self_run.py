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
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.evidence.ports import chip_photo, trust_config, verify_chip
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
from zid_platform.modules.verification.application import biometric_eligibility, service
from zid_platform.modules.verification.application.identity_signal_feed import observe_request
from zid_platform.modules.verification.domain.biometric_methods import Method
from zid_platform.modules.verification.domain.self_run import ChipEvidence
from zid_platform.modules.verification.domain.verification import VerificationError
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
    config = getattr(request.app.state, "trust_config", None) or trust_config()
    verdict = verify_chip(sod, groups, csca=config.csca, crls=config.crls)
    chip = ChipEvidence.from_verdict(verdict.status, verdict.reasons, verdict.detail)
    verified = verdict.detail.get("data_groups_verified")
    covered = {int(g) for g in verified} if isinstance(verified, list | tuple) else set()
    active = None
    if body.active_authentication is not None:
        active = service.ActiveAuthInput(
            response=_b64(body.active_authentication.response_base64),
            dg15=groups.get(15, b""),
            dg15_verified=15 in covered and 15 in groups,
            hash_name=body.active_authentication.hash_name,
        )
    biometric = None
    engine = getattr(request.app.state, "biometric_engine", None)
    photo = (
        chip_photo(groups[2]) if (2 in covered and 2 in groups and chip.photo_verified) else None
    )
    allowed: dict[str, Any] | None = None
    if body.face is not None and engine is not None and photo is not None:
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
                engine, _b64(body.face.probe_base64), _b64(body.face.liveness_base64), photo
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
