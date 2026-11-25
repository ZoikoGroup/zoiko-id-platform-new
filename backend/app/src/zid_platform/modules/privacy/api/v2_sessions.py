"""/v2/verification-sessions: the privacy-safe SDK session (ZID-PRI-004 section 07).

    POST /v2/verification-sessions                         [purpose_id, region, subject_ref, assurance_goal, alternative_mode] default deny;
                                                           with a market (WF-01), a hosted session: a one-use launch link for the person
    POST /v2/verification-sessions/{id}/resume-token       the business asks for a fresh link; every earlier one stops working (WF-01 S12)
    (the person's own hosted endpoints are in verification/api/applicant.py)
    POST /v2/verification-sessions/{id}/consent-records    only where a valid consent-based route applies, only by the person
    POST /v2/verification-sessions/{id}/cancel             stops capture; nothing more is collected
    POST /v2/verification-sessions/{id}/route              the person chooses a non-biometric alternative, or the camera again (CR-108, WF-04 B02)
    GET  /v2/privacy/assisted-routes | POST .../{id}/assisted:claim | :complete   the assisted route's back office (CR-110, WF-04 B02)
    GET  /v2/verification-sessions/{id}/subject-status     whether capture may happen NOW, recomputed from live records, and why not
(notice acknowledgments and alternatives are in privacy/api/v2.py)

The server evaluates the lawful basis and the notice before enabling capture. It returns no image, template, score or risk-model output.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.api_auth import Caller, authenticated_caller
from zid_platform.modules.privacy.api.v2 import CODE, FULFIL, _ids, _me, _run, _tenant
from zid_platform.modules.privacy.application import applicant_session, sessions
from zid_platform.modules.privacy.domain.model import PrivacyError

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
    # WF-01: a hosted session for the person, opened by the business's server. The market whose identity proofing rules apply, where the person goes back
    # afterwards (an https address on one of the business's verified domains) and an opaque value echoed back there.
    market: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    return_url: str | None = Field(default=None, max_length=2000)
    return_state: str | None = Field(default=None, max_length=200)


@router.post("/v2/verification-sessions", status_code=201)
async def create_session(body: SessionIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    me = _me(caller)
    environment = str(getattr(request.app.state, "environment", "production"))
    now = datetime.now(UTC)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        # WF-01 §04, Q04, S10: the market's regulatory checks and the return address, before a session exists (the gate is wired by main)
        mode = await applicant_session.hosted_gate(request, conn, body.market, body.return_url) if body.market else None
        if body.return_url and not body.market:
            raise PrivacyError("RETURN_URL_NOT_ALLOWED", "a return address is only for a hosted session")
        out = await sessions.open_session(
            conn, _tenant(caller), me,
            activity_code=body.activity_code, purpose_code=body.purpose_id,
            data_category=body.data_category, subject_ref=body.subject_ref,
            country=body.region, subregion=body.subregion, assurance_goal=body.assurance_goal,
            alternative_mode=body.alternative_mode, notice_version=body.notice_version,
            provider_service_ref=body.provider_service_ref, pack_id=body.jurisdiction_pack_id,
            market=body.market, environment=environment if body.market else None, return_url=body.return_url,
            return_state=body.return_state, expires_at=now + applicant_session.SESSION_TTL if body.market else None,
        )  # fmt: skip
        if not body.market:
            return out
        launch = await applicant_session.issue_launch(
            conn, _tenant(caller), out["session_ref"], now
        )
        await emit(
            conn, name="verification.session.created", tenant_id=_tenant(caller), aggregate_type="verification_session",
            aggregate_id=out["session_ref"], aggregate_version=await applicant_session.next_event_version(conn, out["session_ref"]),
            data={"session_ref": out["session_ref"], "market": body.market, "region": body.region, "purpose_code": body.purpose_id,
                  "notice_version": body.notice_version, "environment": environment,
                  "expires_at": (now + applicant_session.SESSION_TTL).isoformat()},  # the maturity horizon analytics counts a session lost by
        )  # fmt: skip
        base = str(getattr(request.app.state, "hosted_verify_base", "")).rstrip("/")
        return {
            **out,
            "mode": mode,
            # the token rides in the fragment, which browsers never send to a server or write to its logs
            "launch_url": f"{base}/verify-session#t={launch['token']}",
            "launch_token": launch["token"],
            "launch_expires_at": launch["expires_at"],
        }

    # the person (or the integrator acting for them) opens their own session: no administrator role is involved
    return {**_ids(), **await _run(request, caller, None, "", go)}


@router.post("/v2/verification-sessions/{session_ref}/resume-token", status_code=201)
async def resume_token(session_ref: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    """WF-01 S12: the business asks for a fresh one-use link for a person who left. Every earlier link and session credential stops working."""
    _me(caller)
    now = datetime.now(UTC)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        launch = await applicant_session.issue_launch(
            conn, _tenant(caller), session_ref, now, revoke_earlier=True
        )
        base = str(getattr(request.app.state, "hosted_verify_base", "")).rstrip("/")
        return {"session_ref": session_ref, "launch_url": f"{base}/verify-session#t={launch['token']}", "launch_token": launch["token"],
                "launch_expires_at": launch["expires_at"]}  # fmt: skip

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


class SessionRouteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = Field(
        pattern=r"^[A-Z][A-Z0-9_]{1,63}$"
    )  # an alternative id, or NONE for the camera route


@router.post("/v2/verification-sessions/{session_ref}/route")
async def choose_route(
    session_ref: str, body: SessionRouteIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await sessions.choose_alternative(
            conn, _tenant(caller), me, session_ref, mode=body.mode
        )

    # the person chooses their own route: no administrator role is involved
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


# ---------------------------------------------------------------------------------------------------------------- assisted route back office (CR-110)


@router.get("/v2/privacy/assisted-routes")
async def assisted_queue(
    request: Request,
    caller: CallerDep,
    state: Literal["WAITING", "IN_PROGRESS", "DONE", "NOT_COMPLETED"] | None = None,
) -> dict[str, Any]:
    """People who chose a non-biometric way and are waiting for a person (WF-04 B02)."""

    async def go(conn: AsyncConnection) -> list[dict[str, Any]]:
        return await sessions.list_assisted(
            conn, states=(state,) if state else ("WAITING", "IN_PROGRESS")
        )

    return {**_ids(), "items": await _run(request, caller, FULFIL, "verification_session:*", go)}


@router.post("/v2/verification-sessions/{session_ref}/assisted:claim")
async def assisted_claim(session_ref: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await sessions.claim_assisted(conn, _tenant(caller), me, session_ref)

    return {
        **_ids(),
        **await _run(request, caller, FULFIL, f"verification_session:{session_ref}", go),
    }


class AssistedDoneIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    done: bool
    outcome_ref: str | None = Field(default=None, max_length=200)
    reason_code: str | None = Field(default=None, pattern=CODE)


@router.post("/v2/verification-sessions/{session_ref}/assisted:complete")
async def assisted_complete(
    session_ref: str, body: AssistedDoneIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await sessions.complete_assisted(
            conn,
            _tenant(caller),
            me,
            session_ref,
            done=body.done,
            outcome_ref=body.outcome_ref,
            reason_code=body.reason_code,
        )

    return {
        **_ids(),
        **await _run(request, caller, FULFIL, f"verification_session:{session_ref}", go),
    }
