"""WF-05 device risk, tenant API (ZID-DTV-WF-05 §19 POST /v1/device-observations, DTV-22; QA-030 to QA-035; CR-116).

    POST /v1/verification-cases/{id}/device-nonces     a server nonce for a platform attestation          verification:execute
    POST /v1/device-observations                       one observation (Idempotency-Key)                  verification:execute

Only with DEVICE_RISK cleared for the case: otherwise refused and nothing is written. The device reference is hashed into a tenant- and period-keyed
pseudonym at once and never returned or kept. No answer carries a fingerprint, a supplier payload or a score.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Path, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError
from zid_idempotency import Scope, begin, complete

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verification.api.verification import _scope
from zid_platform.modules.verification.application import device_checks as svc
from zid_platform.modules.verification.domain import check_cases as cc
from zid_platform.modules.verification.domain import device_risk as dr

router = APIRouter(tags=["verification"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
Id = Annotated[str, Path(pattern=r"^[0-9a-fA-F-]{36}$")]
_STATUS = {
    "CASE_NOT_FOUND": 404,
    "CASE_CLOSED": 409,
    "METHOD_NOT_AVAILABLE_FOR_CASE": 409,
    "NONCE_REPLAYED": 409,
    "NONCE_EXPIRED": 409,
}


class NonceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_ref: str = Field(min_length=1, max_length=128)


class ObservationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    session_ref: str = Field(min_length=1, max_length=128)
    platform: Literal["WEB", "ANDROID", "IOS"]
    device_ref: str = Field(
        min_length=8,
        max_length=256,
        description="Your app's device reference; hashed at once, never kept.",
    )
    signals: list[
        Literal[
            "EMULATOR", "ROOTED_OR_JAILBROKEN", "APP_INTEGRITY_FAILED", "AUTOMATION", "VPN_OR_PROXY"
        ]
    ] = Field(default_factory=list, max_length=5)
    attestation_token: str | None = Field(default=None, max_length=16384)
    nonce: str | None = Field(default=None, max_length=128)


def _refuse(e: Exception) -> ZidError:
    code = str(getattr(e, "code", "DEVICE_OBSERVATION_REFUSED"))
    return ZidError(
        code if code in _STATUS else "DEVICE_OBSERVATION_REFUSED",
        str(e).split(":")[0],
        http_status=_STATUS.get(code, 422),
    )


@router.post("/v1/verification-cases/{case_id}/device-nonces", status_code=201)
async def nonce(case_id: Id, body: NonceIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:execute")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            return await svc.new_nonce(
                conn, case_id, session_ref=body.session_ref, now=datetime.now(UTC)
            )
        except (cc.CaseError, dr.DeviceError) as e:
            raise _refuse(e) from e


@router.post("/v1/device-observations", status_code=201)
async def observe(
    body: ObservationIn,
    request: Request,
    caller: CallerDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> dict[str, Any]:
    _scope(caller, "verification:execute")
    scope = Scope(caller.ctx.tenant_id or "", caller.client_id, "POST /v1/device-observations")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        stored = await begin(
            conn, scope=scope, key=idempotency_key, payload=body.model_dump(mode="json")
        )
        if stored is not None:
            return dict(stored.body)
        try:
            out = await svc.observe(
                conn, body.case_id, session_ref=body.session_ref, platform=dr.Platform(body.platform), device_ref=body.device_ref,
                reported=tuple(dr.Signal(s) for s in body.signals), attestation_token=body.attestation_token, nonce=body.nonce,
                verifier=getattr(request.app.state, "attestation_verifier", None), now=datetime.now(UTC),
            )  # fmt: skip
        except (cc.CaseError, dr.DeviceError) as e:
            raise _refuse(e) from e
        await complete(conn, scope=scope, key=idempotency_key, status=201, body=out)
    return out
