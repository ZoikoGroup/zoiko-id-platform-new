"""WF-05 contacts and one-time challenges on a verification case, tenant API (ZID-DTV-WF-05 §19, §20, §22, A02-A04; CR-114).

    POST /v1/verification-cases/{id}/contacts                       add or change the case's email or phone        verification:execute
    POST /v1/contacts/{id}/challenges                               start a challenge (Idempotency-Key)            verification:execute
    POST /v1/contacts/{id}/challenges/{challenge_id}:resend         a new code, same expiry                        verification:execute
    POST /v1/contacts/{id}/confirm                                  check the code or link secret                  verification:execute

No response ever carries the full address or number, or the code. The destination and the code go to the deployment's challenge sender after the
transaction commits (`app.state.contact_challenge_sender`); none ships, so a deployment without one sends nothing (and its challenge methods have no
adapter in `config/verification/check-adapters.yaml`). A limit, an unknown contact and a wrong code each answer with one stable code.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Protocol

from fastapi import APIRouter, Depends, Header, Path, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError
from zid_idempotency import Scope, begin, complete

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verification.api.verification import _scope
from zid_platform.modules.verification.application import contact_challenges as svc
from zid_platform.modules.verification.domain import check_cases as cc
from zid_platform.modules.verification.domain import contact_challenges as ch
from zid_platform.modules.verification.infrastructure import check_method_config

log = logging.getLogger("zid.verification.contacts")
router = APIRouter(tags=["verification"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
Id = Annotated[str, Path(pattern=r"^[0-9a-fA-F-]{36}$")]
_STATUS = {
    "CASE_NOT_FOUND": 404, "CONTACT_NOT_FOUND": 404, "CHALLENGE_NOT_FOUND": 404, "CASE_CLOSED": 409, "CONTACT_SUPERSEDED": 409,
    "CHALLENGE_FINAL": 409, "CHALLENGE_EXPIRED": 409, "CHALLENGE_RATE_LIMITED": 429, "METHOD_NOT_AVAILABLE_FOR_CASE": 409,
    "CHANNEL_NOT_ALLOWED_FOR_DESTINATION": 409,
}  # fmt: skip


class ChallengeSender(Protocol):
    async def send(self, delivery: svc.Delivery) -> None: ...


class ContactIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["EMAIL", "PHONE"]
    value: str = Field(min_length=3, max_length=254)
    region: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")


class ChallengeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel: Literal["EMAIL", "SMS", "VOICE"]
    session_ref: str = Field(
        min_length=1, max_length=128, description="Your reference for the applicant's session."
    )


class ResendIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_ref: str = Field(min_length=1, max_length=128)


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    code: str = Field(min_length=6, max_length=64)
    session_ref: str = Field(min_length=1, max_length=128)


def _refuse(e: ch.ChallengeError | cc.CaseError) -> ZidError:
    return ZidError(
        e.code if e.code in _STATUS else "CONTACT_CHALLENGE_REFUSED",
        str(e).split(":")[0],
        http_status=_STATUS.get(e.code, 422),
    )


async def _deliver(request: Request, delivery: svc.Delivery) -> None:
    """After the transaction. A missing sender or a delivery problem is logged without the destination and never changes the answer."""
    sender: ChallengeSender | None = getattr(request.app.state, "contact_challenge_sender", None)
    if sender is None:
        log.warning(
            "contact challenge %s not sent: no challenge sender is configured",
            delivery.challenge_id[:8],
        )
        return
    try:
        await sender.send(delivery)
    except Exception:  # noqa: BLE001 - the person can ask for a resend
        log.warning(
            "contact challenge %s delivery failed", delivery.challenge_id[:8], exc_info=False
        )


def _source(channel: ch.Channel) -> str:
    adapter = check_method_config.adapters().get(svc.METHOD[channel])
    return (
        f"{adapter.engine_id}@{adapter.engine_version}"
        if adapter
        else f"zid-{channel.value.lower()}-challenge"
    )


@router.post("/v1/verification-cases/{case_id}/contacts", status_code=201)
async def add_contact(
    case_id: Id, body: ContactIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    _scope(caller, "verification:execute")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            c = await svc.add_contact(
                conn,
                case_id,
                kind=ch.ContactKind(body.type),
                raw=body.value,
                region=body.region,
                now=datetime.now(UTC),
            )
        except (ch.ChallengeError, cc.CaseError) as e:
            raise _refuse(e) from e
    return svc.contact_view(c)


@router.post("/v1/contacts/{contact_id}/challenges", status_code=202)
async def start(
    contact_id: Id,
    body: ChallengeIn,
    request: Request,
    caller: CallerDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> dict[str, Any]:
    _scope(caller, "verification:execute")
    scope = Scope(caller.ctx.tenant_id or "", caller.client_id, "POST /v1/contacts/{id}/challenges")
    payload = {"contact": contact_id, **body.model_dump(mode="json")}
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        stored = await begin(conn, scope=scope, key=idempotency_key, payload=payload)
        if stored is not None:
            return dict(stored.body)  # a replay sends nothing again
        try:
            challenge, delivery = await svc.start(
                conn,
                contact_id,
                channel=ch.Channel(body.channel),
                session_ref=body.session_ref,
                now=datetime.now(UTC),
            )
            contact = await svc._load_contact(conn, contact_id)
        except (ch.ChallengeError, cc.CaseError) as e:
            raise _refuse(e) from e
        out = svc.challenge_view(challenge, contact)
        await complete(conn, scope=scope, key=idempotency_key, status=202, body=out)
    await _deliver(request, delivery)
    return out


@router.post("/v1/contacts/{contact_id}/challenges/{challenge_id}:resend", status_code=202)
async def resend(
    contact_id: Id, challenge_id: Id, body: ResendIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    _scope(caller, "verification:execute")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            challenge, delivery = await svc.resend(
                conn, contact_id, challenge_id, session_ref=body.session_ref, now=datetime.now(UTC)
            )
        except (ch.ChallengeError, cc.CaseError) as e:
            raise _refuse(e) from e
    await _deliver(request, delivery)
    return svc.challenge_view(challenge)


@router.post("/v1/contacts/{contact_id}/confirm")
async def confirm(
    contact_id: Id, body: ConfirmIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    _scope(caller, "verification:execute")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            ch_row = await svc._load_challenge(conn, body.challenge_id, contact_id)
            outcome = await svc.confirm(
                conn, contact_id, body.challenge_id, body.code, session_ref=body.session_ref, source=_source(ch_row.channel),
                now=datetime.now(UTC),
            )  # fmt: skip
        except (ch.ChallengeError, cc.CaseError) as e:
            raise _refuse(e) from e
    view = svc.challenge_view(outcome.challenge)
    if outcome.verified:
        return {
            **view,
            "assertion": "CONTACT_CONTROL_CONFIRMED",
            "note": "Shows control of this inbox or number now; not who the person is.",
        }
    # the attempt is already counted (the transaction committed); the same message for every reason
    raise ZidError(
        outcome.reason or "CHALLENGE_INVALID",
        f"The code was not accepted. Attempts left: {view['attempts_left']}.",
        http_status=422,
    )
