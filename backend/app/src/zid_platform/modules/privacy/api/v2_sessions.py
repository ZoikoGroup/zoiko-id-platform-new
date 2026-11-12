"""/v2/verification-sessions: the privacy-safe SDK session (ZID-PRI-004 section 07).

    POST /v2/verification-sessions                         [purpose_id, region, subject_ref, assurance_goal, alternative_mode] default deny
    POST /v2/verification-sessions/{id}/consent-records    only where a valid consent-based route applies, only by the person
    POST /v2/verification-sessions/{id}/cancel             stops capture; nothing more is collected
    GET  /v2/verification-sessions/{id}/subject-status     whether capture may happen NOW, recomputed from live records, and why not
(notice acknowledgments and alternatives are in privacy/api/v2.py)

The server evaluates the lawful basis and the notice before enabling capture. It returns no image, template, score or risk-model output.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.api_auth import Caller, authenticated_caller
from zid_platform.modules.privacy.api.v2 import CODE, _ids, _me, _run, _tenant
from zid_platform.modules.privacy.application import sessions

router = APIRouter(tags=["privacy-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


class SessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose_id: str = Field(pattern=CODE)  # the purpose code
    activity_code: str = Field(pattern=CODE)
    data_category: Literal["D0", "D1", "D2", "D3", "D4"] = "D3"
    region: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    subregion: str | None = Field(default=None, max_length=10)
    subject_ref: str | None = Field(default=None, max_length=100)
    assurance_goal: str | None = Field(default=None, max_length=64)
    alternative_mode: str | None = Field(default=None, max_length=64)
    notice_version: str | None = Field(default=None, max_length=100)
    provider_service_ref: str | None = Field(default=None, max_length=200)
    jurisdiction_pack_id: str | None = Field(default=None, max_length=64)


@router.post("/v2/verification-sessions", status_code=201)
async def create_session(body: SessionIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await sessions.open_session(
            conn, _tenant(caller), me,
            activity_code=body.activity_code, purpose_code=body.purpose_id,
            data_category=body.data_category, subject_ref=body.subject_ref,
            country=body.region, subregion=body.subregion, assurance_goal=body.assurance_goal,
            alternative_mode=body.alternative_mode, notice_version=body.notice_version,
            provider_service_ref=body.provider_service_ref, pack_id=body.jurisdiction_pack_id,
        )  # fmt: skip

    # the person (or the integrator acting for them) opens their own session: no administrator role is involved
    return {**_ids(), **await _run(request, caller, None, "", go)}


class SessionConsentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notice_version: str = Field(min_length=1, max_length=64)
    capture_method: str = Field(pattern="^(WEB|MOBILE|API|PAPER_VERIFIED|OTHER)$")
    affirmative_action: str = Field(min_length=3, max_length=128)


@router.post("/v2/verification-sessions/{session_ref}/consent-records", status_code=201)
async def session_consent(
    session_ref: str, body: SessionConsentIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await sessions.record_consent(
            conn, _tenant(caller), me, session_ref, **body.model_dump()
        )

    return {**_ids(), **await _run(request, caller, None, "", go)}


@router.post("/v2/verification-sessions/{session_ref}/cancel")
async def cancel_session(session_ref: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await sessions.cancel(conn, _tenant(caller), session_ref)

    return {**_ids(), **await _run(request, caller, None, "", go)}


@router.get("/v2/verification-sessions/{session_ref}/subject-status")
async def subject_status(session_ref: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await sessions.status(conn, _tenant(caller), session_ref)

    return {**_ids(), **await _run(request, caller, None, "", go)}
