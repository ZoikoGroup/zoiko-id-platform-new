"""Person-first sign-up API (ZID-UX-WF-013A; slice L-11, CR-048).

    GET  /v1/registration/capabilities?country=XX   what this country and journey can do here, honestly; a signed anti-abuse nonce
    POST /v1/registration/intents                   country, journey, email -> one generic answer, whatever the address is
    POST /v1/registration/resend                    a limited, generic resend that ends the earlier link
    POST /v1/registration/confirm                   the single-use link: proves the inbox, makes the account, issues an enrollment ticket
    GET  /v1/registration/status?request_id=        the safe next step; never a score, a secret or an account-existence signal

Unauthenticated by design (these are the way in for someone with no account yet), off unless the cell has a registration tenant, rate
limited per address, and never trusting the caller for a tenant, role, assurance level, verification status or region. The passkey
ceremony is the existing enrollment-ticket flow, and a provisional organization claim is `POST /v1/org-claims` (slice L-12), which the
account can make only after it has an authenticator.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_errors import ZidError

from zid_platform.api_auth import request_engine
from zid_platform.attempt_context import attempt_inputs
from zid_platform.modules.communication.ports import Recipient, address_hash, send_email
from zid_platform.modules.onboarding.application import registration as svc
from zid_platform.modules.onboarding.domain.registration import (
    EXPIRED_MESSAGE,
    GENERIC_MESSAGE,
    RegistrationError,
)
from zid_platform.modules.tenant.ports import ConsoleClient

router = APIRouter(tags=["registration"])
log = logging.getLogger("zid.registration")

WINDOW_S = 600.0
LIMITS = {
    "intents": 10,
    "confirm": 20,
    "resend": 10,
    "status": 120,
    "capabilities": 120,
    "sign_in_workspace": 10,
    "recovery": 10,
    "recovery_confirm": 20,
}
_buckets: dict[str, dict[str, deque[float]]] = defaultdict(lambda: defaultdict(deque))
TEMPLATE_VERIFY = "ZID-EM-001"
TEMPLATE_SIGN_IN_LINK = "ZID-EM-010"  # a single-use link to create a new passkey and sign in
TEMPLATE_RECOVERY_STARTED = "ZID-EM-013"  # an administrator's recovery keeps its gates: the email is only a notice
_NAMESPACE = uuid.UUID("5c6f6f3e-3f5a-4d0a-9d4e-4f1d8b2a7c11")


MAX_TRACKED_CLIENTS = (
    50_000  # per bucket: a spray of addresses can never grow the process without bound
)


def _sweep(table: dict[str, deque[float]], now: float) -> None:
    """Forget clients whose window has passed, then (only if every one is still live) the oldest, so the table stays bounded."""
    for key in [k for k, q in table.items() if not q or now - q[-1] > WINDOW_S]:
        del table[key]
    while len(table) > MAX_TRACKED_CLIENTS:
        del table[next(iter(table))]


def _limit(request: Request, bucket: str) -> None:
    """In-process per-address bound, a backstop behind the edge rate limiter (DEC-G1) and the per-address caps kept in the database.
    The address is the real client's only when the proxy in front is trusted (`ZID_FORWARDED_ALLOW_IPS`, see the supervisor); without
    that, every caller behind one ingress would share one bucket. Always answers the same way."""
    key = request.client.host if request.client else "unknown"
    table = _buckets[bucket]
    q = table[key]
    now = time.monotonic()
    while q and now - q[0] > WINDOW_S:
        q.popleft()
    if len(table) > MAX_TRACKED_CLIENTS:
        _sweep(table, now)
    if len(q) >= LIMITS[bucket]:
        raise ZidError(
            "RATE_LIMITED",
            "Too many requests from this address; try again later.",
            http_status=429,
            retryable=True,
            retry_after_ms=60_000,
        )
    q.append(now)


def _env(request: Request) -> svc.Env:
    tenant = getattr(request.app.state, "registration_tenant", None)
    if not tenant:
        raise ZidError("REGISTRATION_DISABLED", "Sign-up is not enabled here.", http_status=403)
    return svc.Env(
        registration_tenant=str(tenant),
        here=str(getattr(request.app.state, "home_cell", None) or "local-1"),
        notice_version=str(getattr(request.app.state, "tenant_signup_notice_version", "")),
        environment=str(getattr(request.app.state, "environment", "production")),
        console=ConsoleClient(*console)
        if (console := getattr(request.app.state, "tenant_console", None))
        else None,
        cell_endpoints=dict(getattr(request.app.state, "cell_endpoints", {}) or {}),
    )


_STATUS = {
    "COUNTRY_INVALID": 422,
    "EMAIL_INVALID": 422,
    "INTENT_INVALID": 422,
    "NOTICE_VERSION_STALE": 422,
    "REGION_UNAVAILABLE": 422,
    "COUNTRY_CAPABILITY_UNAVAILABLE": 422,
    "CELL_ROUTING_REQUIRED": 421,
    "LINK_INVALID": 410,
    "REQUEST_NOT_FOUND": 404,
    "REGISTRATION_DISABLED": 403,
}


def _http(e: RegistrationError) -> ZidError:
    if e.code == "LINK_INVALID":  # every way a link can fail is this one message
        return ZidError(e.code, EXPIRED_MESSAGE, http_status=410)
    if e.code == "CELL_ROUTING_REQUIRED":
        where = f" at {e.detail}" if e.detail.startswith("http") else ""
        return ZidError(
            e.code, f"Your account is held in a different region{where}.", http_status=421
        )
    return ZidError(
        e.code,
        e.detail or e.code.replace("_", " ").capitalize() + ".",
        http_status=_STATUS.get(e.code, 409),
    )


async def _send(request: Request, env: svc.Env, job: svc.SendJob) -> None:
    """After the response: the same for every request, so a known address cannot be told from an unknown one by how long the answer took.
    A delivery problem is logged without the address and never changes what the caller was told."""
    cfg = getattr(request.app.state, "email_config", None)
    sender = getattr(request.app.state, "email_sender", None)
    if cfg is None or sender is None:
        log.warning("registration email not sent: email is not configured")
        return
    try:
        recipient = Recipient(
            job.address,
            await address_hash(job.address),
            None,
            "UTC",
            job.locale if job.locale != "und" else "en-US",
        )
        await send_email(
            request_engine(request),
            cfg,
            sender,
            cell_id=env.here,
            template_id=TEMPLATE_VERIFY,
            event_id=str(uuid.uuid5(_NAMESPACE, f"{job.request_ref}:{job.sequence}")),
            event_type="identity.enrollment.verification_requested",
            tenant_id=env.registration_tenant,
            recipient=recipient,
            merge={
                "single_use_verification_url": job.token,
                "expires_at_local": job.expires_at,
            },
        )
    except Exception:  # noqa: BLE001 - delivery must not change the response; the person can ask for a new link
        log.warning("registration email failed for request %s", job.request_ref[:6], exc_info=False)


async def _send_recovery(request: Request, env: svc.Env, job: svc.RecoveryJob) -> None:
    """After the response, like `_send`: the same for every request, and a delivery problem never changes what the caller was told."""
    cfg = getattr(request.app.state, "email_config", None)
    sender = getattr(request.app.state, "email_sender", None)
    if cfg is None or sender is None:
        log.warning("recovery email not sent: email is not configured")
        return
    now = datetime.now(UTC)
    link = job.token is not None
    try:
        await send_email(
            request_engine(request),
            cfg,
            sender,
            cell_id=env.here,
            template_id=TEMPLATE_SIGN_IN_LINK if link else TEMPLATE_RECOVERY_STARTED,
            event_id=str(uuid.uuid4()),
            event_type="authentication.recovery.email_requested",
            tenant_id=job.tenant_id,
            recipient=Recipient(job.address, await address_hash(job.address), None, "UTC", job.locale),
            merge=(
                {"single_use_signin_url": job.token, "expires_at_local": job.expires_at}
                if link
                else {"event_at_local": now}
            ),
        )
    except Exception:  # noqa: BLE001 - delivery must not change the response; the person can ask again
        log.warning("recovery email failed", exc_info=False)


# ------------------------------------------------------------------------------------------- capabilities


@router.get("/v1/registration/capabilities")
async def capabilities(request: Request, country: str | None = None) -> dict[str, Any]:
    _limit(request, "capabilities")
    env = _env(request)
    try:
        return await svc.capabilities(env, country, now=datetime.now(UTC))
    except RegistrationError as e:
        raise _http(e) from e


# ----------------------------------------------------------------------------------------------- intents


class IntentBody(BaseModel):
    """What the person typed. Nothing here can name a tenant, a role, an assurance level, a verification status or a home region."""

    model_config = ConfigDict(extra="forbid")
    country: str = Field(min_length=2, max_length=2)
    journey: str = Field(min_length=1, max_length=16)
    email: str = Field(min_length=3, max_length=254)
    locale: str | None = Field(default=None, max_length=35)
    notice_version: str = Field(min_length=1, max_length=40)
    nonce: str | None = Field(default=None, max_length=200)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)
    # the request this browser already holds, when the person is changing the address: ends it, names nothing else
    replaces: str | None = Field(default=None, min_length=1, max_length=64)


@router.post("/v1/registration/intents", status_code=202)
async def create_intent(
    body: IntentBody,
    request: Request,
    background: BackgroundTasks,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=128)] = None,
) -> dict[str, Any]:
    _limit(request, "intents")
    env = _env(request)
    try:
        async with request_engine(request).begin() as conn:
            got = await svc.initiate(
                conn,
                env,
                country=body.country,
                journey=body.journey,
                email=body.email,
                locale=body.locale,
                notice_version=body.notice_version,
                nonce=body.nonce,
                idempotency_key=idempotency_key or body.idempotency_key,
                now=datetime.now(UTC),
                replaces=body.replaces,
            )
            # CR-068: count this sign-up attempt against its contact, network and device (best effort, never blocks the journey)
            from zid_db import in_tenant

            from zid_platform.modules.screening.ports import AttemptAction, note_attempt

            async with in_tenant(conn, env.registration_tenant):
                await note_attempt(
                    conn,
                    env.registration_tenant,
                    subject_ref=f"registration:{got.request_ref[:24]}",
                    action=AttemptAction.SIGNUP,
                    now=datetime.now(UTC),
                    contact=body.email,
                    **attempt_inputs(request),
                )
    except RegistrationError as e:
        raise _http(e) from e
    if got.job is not None:
        background.add_task(_send, request, env, got.job)
    return {
        "request_id": got.request_ref,
        "status": "VERIFICATION_REQUESTED",
        "message": GENERIC_MESSAGE,
    }


class ResendBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=64)


class SignInWorkspaceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)


@router.post("/v1/registration/sign-in-workspace")
async def sign_in_workspace(body: SignInWorkspaceBody, request: Request) -> dict[str, Any]:
    """Which workspace to sign in to, from the address the account was made with. Ten asks per ten minutes per client; the answer is only a workspace name."""
    _limit(request, "sign_in_workspace")
    try:
        async with request_engine(request).begin() as conn:
            workspace = await svc.workspace_for_email(conn, _env(request), email=body.email)
    except RegistrationError as e:
        raise _http(e) from e
    return {"workspace": workspace}


class RecoveryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)


@router.post("/v1/registration/recovery", status_code=202)
async def recovery(body: RecoveryBody, request: Request, background: BackgroundTasks) -> dict[str, Any]:
    """"I can't sign in": email the account's own address a way back. Ten asks per ten minutes per client, and the answer is the same
    whether or not the address has an account."""
    _limit(request, "recovery")
    env = _env(request)
    async with request_engine(request).begin() as conn:
        job = await svc.recovery_request(conn, env, email=body.email, now=datetime.now(UTC))
    if job is not None:
        background.add_task(_send_recovery, request, env, job)
    return {"status": "RECOVERY_REQUESTED", "message": GENERIC_MESSAGE}


class RecoveryConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=200)


@router.post("/v1/registration/recovery/confirm")
async def recovery_confirm(body: RecoveryConfirmBody, request: Request) -> dict[str, Any]:
    """The emailed recovery link: which workspace, and the one-passkey enrollment ticket (shown once). Every failure is the same message."""
    _limit(request, "recovery_confirm")
    try:
        async with request_engine(request).begin() as conn:
            out = await svc.recovery_confirm(conn, _env(request), body.token, now=datetime.now(UTC))
    except RegistrationError as e:
        raise _http(e) from e
    return {
        "request_id": out.request_ref,
        "account": out.account,
        "identity": out.identity,
        "next_step": "ENROLL_AUTHENTICATOR",
        "then": out.route,
        "enrollment_ticket": out.enrollment_ticket,
        "enrollment_ticket_expires_at": out.ticket_expires_at.isoformat(),
        "console_client_id": out.console_client_id,
        "workspace": out.workspace,
        "address": out.masked_address,
        "authenticator_fallback": "GOVERNED_RECOVERY",
    }


@router.post("/v1/registration/resend", status_code=202)
async def resend(body: ResendBody, request: Request, background: BackgroundTasks) -> dict[str, Any]:
    _limit(request, "resend")
    env = _env(request)
    async with request_engine(request).begin() as conn:
        job = await svc.resend(conn, env, body.request_id, now=datetime.now(UTC))
    if job is not None:
        background.add_task(_send, request, env, job)
    return {
        "status": "VERIFICATION_REQUESTED",
        "message": GENERIC_MESSAGE,
    }  # the same whether or not anything was sent


# ----------------------------------------------------------------------------------------------- confirm


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str | None = Field(
        default=None, min_length=1, max_length=64
    )  # the emailed link carries only the token
    token: str = Field(min_length=1, max_length=200)


@router.post("/v1/registration/confirm")
async def confirm(body: ConfirmBody, request: Request) -> dict[str, Any]:
    _limit(request, "confirm")
    env = _env(request)
    refusal: ZidError | None = None
    out: svc.Confirmed | None = None
    async with request_engine(request).begin() as conn:
        try:
            out = await svc.confirm(conn, env, body.request_id, body.token, now=datetime.now(UTC))
        except RegistrationError as e:
            if not e.persist:
                raise _http(e) from e
            refusal = _http(e)  # an attempt was counted: commit it, then refuse
    if refusal is not None:
        raise refusal
    assert out is not None
    return {
        "request_id": out.request_ref,
        "account": out.account,  # an active account ...
        "identity": out.identity,  # ... that is NOT identity verified
        "next_step": "ENROLL_AUTHENTICATOR",
        "then": out.route,
        "enrollment_ticket": out.enrollment_ticket,  # shown once
        "enrollment_ticket_expires_at": out.ticket_expires_at.isoformat(),
        "console_client_id": out.console_client_id,
        "workspace": out.workspace,
        "address": out.masked_address,
        "authenticator_fallback": "GOVERNED_RECOVERY",
    }


# ------------------------------------------------------------------------------------------------ status


def _public_state(state: str) -> str:
    """One public state per outcome the person can see. A known address (nothing sent) and a new one (link sent) are both
    PENDING_VERIFICATION, and the internal hold label is never shown."""
    return {
        "INITIATED": "PENDING_VERIFICATION",
        "VERIFICATION_SENT": "PENDING_VERIFICATION",
        "RISK_HOLD": "ON_HOLD",
    }.get(state, state)


@router.get("/v1/registration/status")
async def registration_status(request: Request, request_id: str) -> dict[str, Any]:
    _limit(request, "status")
    env = _env(request)
    try:
        async with request_engine(request).begin() as conn:
            s = await svc.status(conn, env, request_id, now=datetime.now(UTC))
    except RegistrationError as e:
        raise _http(e) from e
    return {
        "request_id": s.request_ref,
        "state": _public_state(s.state),
        "next_step": s.route.next_step,
        "account": s.route.account,
        "identity": s.route.identity,
        "organization": s.route.organization,
        "message": s.message,
        "expires_at": s.expires_at.isoformat(),
    }
