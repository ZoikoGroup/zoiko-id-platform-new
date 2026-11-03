"""Step-up authentication API and the guard that routes call (ZID-ENG-DEC-009, slice L-9).

    POST /v1/step-up/challenges               open a challenge for an immutable command draft; the server chooses tier and method
    POST /v1/step-up/challenges/{id}/verify   passkey assertion -> a short-lived proof bound to that actor, session and command
    POST /v1/step-up/challenges/{id}/cancel   invalidate the challenge; it grants nothing
    GET  /v1/step-up/tiers                    the tiers and the actions in each, for the UI to explain (never to decide)

`require_step_up` is what a sensitive route calls inside the transaction that executes its command. Enforcement is `off`, `shadow` (a
missing or bad proof is reported and does not block) or `enforce`; `shadow` is the default so existing flows keep working until the owners
switch it on. A proof arrives in the `X-Step-Up-Proof` header, never in a body the route has to parse first.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ErrorDetail, ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.authentication.application import step_up as service
from zid_platform.modules.authentication.application.webauthn import WebAuthnSettings
from zid_platform.modules.authentication.domain.authenticator import AuthnError
from zid_platform.modules.authentication.domain.step_up import StepUpError, Tier, classify
from zid_platform.modules.authentication.infrastructure.step_up_config import step_up_catalogue

router = APIRouter(tags=["step-up"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
log = logging.getLogger("zid.step_up")

PROOF_HEADER = "X-Step-Up-Proof"

_STATUS = {
    "STEP_UP_ACTION_UNKNOWN": 422,
    "STEP_UP_PAYLOAD_INVALID": 422,
    "STEP_UP_HUMAN_REQUIRED": 403,
    "STEP_UP_SESSION_REQUIRED": 403,
    "NO_STRONG_AUTHENTICATOR": 409,
    "STEP_UP_CHALLENGE_INVALID": 404,
    "STEP_UP_CHALLENGE_EXPIRED": 410,
    "STEP_UP_PROOF_CONSUMED": 409,
}


def _http(e: StepUpError | AuthnError) -> ZidError:
    if isinstance(e, AuthnError):
        status = (
            401 if e.code.startswith("WEBAUTHN_") or e.code == "AUTHENTICATOR_NOT_ACTIVE" else 409
        )
        return ZidError(e.code, "Authentication failed.", http_status=status)
    return ZidError(
        e.code,
        e.detail or e.code.replace("_", " ").capitalize() + ".",
        http_status=_STATUS.get(e.code, 403),
    )


def _settings(request: Request) -> WebAuthnSettings:
    s: WebAuthnSettings | None = getattr(request.app.state, "webauthn_settings", None)
    if s is None:
        raise ZidError(
            "DEPENDENCY_UNAVAILABLE", "Passkey authentication is not configured.", http_status=503
        )
    return s


def who_of(caller: Caller) -> service.Who:
    if not caller.ctx.principal_id or not caller.ctx.tenant_id:
        raise ZidError(
            "UNAUTHENTICATED", "An authenticated principal is required.", http_status=401
        )
    return service.Who(
        tenant_id=caller.ctx.tenant_id,
        actor_id=caller.ctx.principal_id,
        session_id=getattr(caller, "session_id", None),
        delegated_actor=getattr(caller, "actor", None),
        workload_bound=bool(getattr(caller, "certificate_thumbprint", None)),
        security_state=getattr(caller, "security_state", "NORMAL"),
    )


class ChallengeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(pattern=r"^[a-z0-9-]{3,120}$")
    resource: str = Field(min_length=1, max_length=256)
    change: dict[str, Any] = Field(default_factory=dict)
    critical: bool = False


class VerifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credential: dict[str, Any]


@router.post("/v1/step-up/challenges", status_code=201)
async def create_challenge(
    body: ChallengeIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    who = who_of(caller)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            return await service.create_challenge(
                conn,
                _settings(request),
                step_up_catalogue(),
                who,
                action_id=body.action,
                resource=body.resource,
                change=body.change,
                critical=body.critical,
                now=datetime.now(UTC),
            )
        except (StepUpError, AuthnError) as e:
            raise _http(e) from e


@router.post("/v1/step-up/challenges/{challenge_id}/verify")
async def verify_challenge(
    challenge_id: str, body: VerifyIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    who = who_of(caller)
    refusal: ZidError | None = None
    out: dict[str, Any] = {}
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            out = await service.verify_challenge(
                conn,
                _settings(request),
                step_up_catalogue(),
                who,
                challenge_id=challenge_id,
                credential=body.credential,
                now=datetime.now(UTC),
            )
        except (StepUpError, AuthnError) as e:
            refusal = _http(e)
            code = e.code
            await service.record_refusal(
                conn, who, action_id="", code=code, now=datetime.now(UTC), target=challenge_id
            )
    if (
        refusal is not None
    ):  # raised after the transaction commits, so the refusal keeps its evidence
        raise refusal
    return out


@router.post("/v1/step-up/challenges/{challenge_id}/cancel")
async def cancel_challenge(
    challenge_id: str, request: Request, caller: CallerDep
) -> dict[str, Any]:
    who = who_of(caller)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            return await service.cancel_challenge(conn, who, challenge_id, datetime.now(UTC))
        except StepUpError as e:
            raise _http(e) from e


@router.get("/v1/step-up/tiers")
async def tiers(request: Request, caller: CallerDep) -> dict[str, Any]:
    """What each tier asks and which actions need it. The UI uses this to explain; the server classifies every command itself."""
    who_of(caller)
    cat = step_up_catalogue()
    return {
        "version": cat.version,
        "tiers": {
            t.value: {
                "max_age_seconds": None if p.max_age is None else int(p.max_age.total_seconds()),
                "single_use": p.single_use,
                "phishing_resistant": p.phishing_resistant,
                "action_bound": p.action_bound,
            }
            for t, p in cat.tiers.items()
        },
        "s2_never_accepts": sorted(cat.s2_forbidden_methods),
        "actions": [
            {
                "action": a.action_id,
                "name": a.name,
                "tier": a.tier.value,
                "escalates_to": a.escalates_to.value if a.escalates_to else None,
                "family": a.family,
                "gate": a.gate,
                "control": a.control,
            }
            for a in cat.actions.values()
        ],
    }


# ------------------------------------------------------------------------------- the guard routes call


def _enforcement(request: Request) -> str:
    return str(getattr(request.app.state, "step_up_enforcement", "shadow"))


async def require_step_up(
    request: Request,
    conn: Any,
    caller: Caller,
    *,
    action: str,
    resource: str,
    change: dict[str, Any],
    critical: bool = False,
    force_enforce: bool = False,
) -> dict[str, Any] | None:
    """Call inside the transaction that executes the command. S0 needs nothing. Otherwise the proof in `X-Step-Up-Proof` must be fresh,
    bound to exactly this command and (for S2) unused; it is spent in this transaction. In `shadow` a missing or bad proof is logged and
    does not block; in `enforce` it is refused with `STEP_UP_REQUIRED` or the specific reason, and the refusal is recorded even though
    this transaction rolls back. `force_enforce` is for commands whose own record forbids a shadow period (an approval vote): the proof is
    required whatever the platform-wide switch says."""
    mode = "enforce" if force_enforce else _enforcement(request)
    if mode == "off":
        return None
    cat = step_up_catalogue()
    who = who_of(caller)
    try:
        cls = classify(cat, action, security_state=who.security_state, critical=critical)
    except StepUpError as e:  # a route naming an unclassified action is a bug, not a customer error
        raise ZidError(e.code, "This action has no step-up classification.", http_status=500) from e
    if cls.tier is Tier.S0:
        return None
    proof = request.headers.get(PROOF_HEADER)
    now = datetime.now(UTC)
    failure: StepUpError | None = None
    if not proof:
        failure = StepUpError("STEP_UP_REQUIRED", "A fresh strong authentication is required.")
    else:
        try:
            return await service.check_and_consume(
                conn,
                cat,
                who,
                proof_id=proof,
                action_id=action,
                resource=resource,
                change=change,
                critical=critical,
                now=now,
            )
        except StepUpError as e:
            failure = e
    assert failure is not None
    if mode != "enforce":
        log.warning(
            "step-up shadow: %s would be refused (%s) for tier %s",
            action,
            failure.code,
            cls.tier.value,
        )
        return None
    async with tenant_transaction(request_engine(request), caller.ctx) as audit_conn:
        await service.record_refusal(
            audit_conn, who, action_id=action, code=failure.code, now=now, target=resource
        )
    max_age = int((cat.tiers[cls.tier].max_age or cat.tiers[Tier.S2].max_age).total_seconds())  # type: ignore[union-attr]
    raise ZidError(
        failure.code if failure.code != "STEP_UP_REQUIRED" else "STEP_UP_REQUIRED",
        "A fresh strong authentication is required.",
        http_status=403,
        headers={
            "WWW-Authenticate": 'Bearer error="insufficient_user_authentication", '
            f'acr_values="urn:zoiko:aal:z3", max_age={max_age}'
        },
        details=[
            ErrorDetail(field="tier", issue=cls.tier.value),
            ErrorDetail(field="action", issue=action),
            ErrorDetail(field="reason", issue=failure.code),
        ],
    )
