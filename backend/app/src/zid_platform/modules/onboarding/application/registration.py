"""Person-first sign-up service (ZID-UX-WF-013A; slice L-11, CR-048).

    capabilities  what this country and journey can do here, honestly, plus a signed anti-abuse nonce
    initiate      country, journey and email become a request; the same generic answer whether or not the address is known
    resend        a limited, generic resend that ends the earlier link
    confirm       the single-use link proves the inbox; the account is created (or the invitation routed) and an enrollment ticket issued
    status        a safe, step-specific next route; never a score, a provider secret or an account-existence signal

The order is the record's: the country is resolved to a home cell and the notice version checked BEFORE anything about the person is
stored (AC-06); the server derives the tenant, role, assurance level, verification status and region, and never takes them from the
caller (the only inputs are country, journey, email, locale, notice version and a nonce). Sign-up produces a person whose account is
ACTIVE and whose identity is NOT verified, who holds no organizational privilege, and whose organization, if they chose that journey, is
only a provisional claim made later through the company-verification flow.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_crypto.handles import handle
from zid_db import in_tenant
from zid_outbox import emit, next_version

from zid_platform.modules.authentication.ports import (
    issue_enrollment_ticket,
    live_recovery_ticket,
    passkey_summary,
    start_email_recovery,
)
from zid_platform.modules.communication.ports import address_hash, set_principal_contact
from zid_platform.modules.evidence.ports import current_runtime
from zid_platform.modules.onboarding.domain.registration import (
    GENERIC_MESSAGE,
    INTENT_TTL,
    MAX_INTENTS_PER_ADDRESS_HOUR,
    NONCE_TTL,
    TOKEN_TTL,
    Journey,
    RegistrationError,
    Route,
    State,
    canonical_email,
    check_token,
    country_of,
    journey_of,
    locale_of,
    mask,
    may_resend,
    new_request_ref,
    new_token,
    request_shaped,
    route_for,
    token_hash,
    token_shaped,
)
from zid_platform.modules.onboarding.infrastructure import registration_store as store
from zid_platform.modules.tenant.ports import (
    ConsoleClient,
    TenantError,
    console_client_for,
    load_tenant,
    roles_of_principal,
    self_signup,
    set_fields,
)
from zid_platform.modules.verification.ports import (
    Capability,
    CountryError,
    openings,
    registry,
    require,
)

log = logging.getLogger("zid.registration")

__all__ = ["GENERIC_MESSAGE", "Env", "SendJob"]

_ROUTING_FILE = (
    Path(__file__).resolve().parents[6] / "config" / "registration" / "country-routing.yaml"
)
PERSONAL_WORKSPACE_NAME = "Personal workspace"


def _routing_table() -> dict[str, list[str]]:
    import os

    root = Path(os.environ.get("ZID_PLATFORM_CONFIG") or _ROUTING_FILE.parents[1])
    doc = (
        yaml.safe_load((root / "registration" / "country-routing.yaml").read_text(encoding="utf-8"))
        or {}
    )
    table = {str(k): [str(c) for c in v] for k, v in (doc.get("routes") or {}).items()}
    # a country an operator has opened names the cell that holds its accounts (CR-127); it replaces the file's row for that country
    table.update(
        {
            code: list(o.home_cells)
            for (code, cap), o in openings().items()
            if cap == "REGISTRATION" and o.status == "OPEN" and o.home_cells
        }
    )
    return table


@dataclass(frozen=True, slots=True)
class Env:
    """What the server knows about this cell, never what the caller says."""

    registration_tenant: str
    here: str
    notice_version: str
    environment: str
    console: ConsoleClient | None
    cell_endpoints: dict[str, str]


@dataclass(frozen=True, slots=True)
class SendJob:
    """The email to send after the transaction commits. The secret exists only here and in the message."""

    request_ref: str
    address: str
    token: str
    expires_at: datetime
    locale: str
    sequence: int


@dataclass(frozen=True, slots=True)
class Routing:
    state: str  # HERE, OTHER_CELL or UNAVAILABLE
    cell: str | None = None
    url: str | None = None


def route_country(env: Env, country: str, table: dict[str, list[str]] | None = None) -> Routing:
    """The home cell for a country from the versioned table. No country is ever defaulted: an unlisted one is UNAVAILABLE."""
    cells = (table if table is not None else _routing_table()).get(country)
    if not cells:
        return Routing("UNAVAILABLE")
    if env.here in cells:
        return Routing("HERE", env.here)
    cell = cells[0]
    return Routing("OTHER_CELL", cell, env.cell_endpoints.get(cell))


# ------------------------------------------------------------------------------------------------ nonce


async def _mac(value: str) -> str:
    rt = current_runtime()
    return await asyncio.to_thread(handle, rt.mac_key, "registration", "nonce", value)


async def make_nonce(now: datetime) -> str:
    """A signed, short-lived value the form carries back. It proves the request came from a form this server served recently; it is a
    proportionate bot control, not an identity, and it never blocks assistive technology (no puzzle, no timing trap)."""
    stamp = str(int(now.timestamp()))
    return f"{stamp}.{await _mac(stamp)}"


async def check_nonce(nonce: str | None, now: datetime) -> bool:
    try:
        stamp, mac = (nonce or "").split(".", 1)
        issued = datetime.fromtimestamp(int(stamp), UTC)
    except (ValueError, OverflowError, OSError):
        return False
    if not timedelta(0) <= now - issued <= NONCE_TTL:
        return False
    return hmac.compare_digest(await _mac(stamp), mac)


# ------------------------------------------------------------------------------------------ capabilities


def _available(country: str, capability: Capability, env: Env, now: datetime) -> bool:
    try:
        require(registry(), country, capability, now=now, environment=env.environment)
    except CountryError:
        return False
    return True


def registration_open(env: Env, country: str, now: datetime) -> bool:
    """Is account registration approved for this country here, now? Anything unlisted, unapproved, not yet effective or only a sandbox in
    a deployed environment is closed. This is the registry of ZID-EDR-001, separate from which cell would hold the account."""
    return _available(country, Capability.REGISTRATION, env, now)


def _default_contact() -> str | None:
    import os

    root = Path(os.environ.get("ZID_PLATFORM_CONFIG") or _ROUTING_FILE.parents[1])
    doc = (
        yaml.safe_load((root / "registration" / "country-routing.yaml").read_text(encoding="utf-8"))
        or {}
    )
    value = doc.get("contact")
    return str(value) if value else None


def _contact(country: str) -> str | None:
    """Where a person goes when registration is unavailable (ZID-EDR-001 section 3): the country's own contact, else the default."""
    c = registry().countries.get(country)
    return (c.contact if c is not None else None) or _default_contact()


async def capabilities(env: Env, country: str | None, *, now: datetime) -> dict[str, Any]:
    out: dict[str, Any] = {
        "notice_version": env.notice_version,
        "locale": "und",
        "nonce": await make_nonce(now),
        "journeys": ["PERSONAL", "ORGANIZATION"],
        "identity_providers": [],  # none is approved and enabled for sign-up yet; an unapproved provider is never shown
        "authenticators": {
            "primary": "PASSKEY",
            "fallback": "GOVERNED_RECOVERY",
        },  # never email or SMS codes
        "country": None,
    }
    if country is None:
        return out
    code = country_of(country)
    routing = route_country(env, code)
    org_open = _available(code, Capability.REGISTRATION, env, now) and _available(
        code, Capability.BUSINESS_VERIFICATION, env, now
    )
    out["country"] = {
        "code": code,
        "account": (
            {
                "HERE": "AVAILABLE",
                "OTHER_CELL": "AVAILABLE_IN_ANOTHER_REGION",
                "UNAVAILABLE": "NOT_AVAILABLE_YET",
            }[routing.state]
            if registration_open(env, code, now)
            else "NOT_AVAILABLE_YET"
        ),
        "contact": _contact(code),
        "routing": {"state": routing.state, "url": routing.url},
        "organization_claims": "AVAILABLE" if org_open else "NOT_AVAILABLE_YET",
        "message": None
        if routing.state != "UNAVAILABLE" and registration_open(env, code, now)
        else "Zoiko iD accounts are not available for this country yet. Nothing has been stored.",
    }
    return out


# ------------------------------------------------------------------------------------------------ events


async def _event(
    conn: AsyncConnection, env: Env, name: str, intent_id: str, data: dict[str, Any]
) -> None:
    """Event ids and safe aggregates only: never an address, a token or a name (EVENTS section 11)."""
    await emit(
        conn,
        name=name,
        tenant_id=env.registration_tenant,
        aggregate_type="registration_intent",
        aggregate_id=intent_id,
        aggregate_version=await next_version(conn, "registration_intent", intent_id),
        data={"intent_id": intent_id, **data},
    )


# ------------------------------------------------------------------------------------------------ initiate


@dataclass(frozen=True, slots=True)
class Initiated:
    request_ref: str
    job: SendJob | None


@dataclass(frozen=True, slots=True)
class RecoveryJob:
    """What to email after a recovery request: a sign-in link (`token` set) for an ordinary member, or a notice (no token) for an
    administrator or lockbox account, whose recovery still needs its gates."""

    address: str
    token: str | None
    expires_at: datetime
    locale: str
    tenant_id: str


_RECOVERY_TOKEN_TENANT = 32  # the link's token is the tenant id (32 hex digits) followed by the one-time enrollment ticket


async def recovery_request(
    conn: AsyncConnection, env: Env, *, email: str, now: datetime
) -> RecoveryJob | None:
    """The person says they cannot sign in and gives the address their account was made with. The caller answers every address the same
    way; only the inbox of a real account receives anything. An ordinary member gets a single-use link to create a new passkey; an
    administrator's or lockbox account's recovery keeps its re-proofing and approval gates, so for those the email is only a notice."""
    address = canonical_email(email)
    email_hash = await address_hash(address)
    async with in_tenant(conn, env.registration_tenant):
        found = await store.account_for(conn, env.registration_tenant, email_hash)
    if found is None:
        return None
    account_tenant, principal = found
    async with in_tenant(conn, account_tenant):
        tenant = await load_tenant(conn, account_tenant)
        if tenant is None or tenant.status.value in ("SUSPENDED", "CLOSED"):
            return None
        progress = await start_email_recovery(
            conn, tenant_id=account_tenant, principal_id=principal, now=now
        )
    if progress is None:
        return None
    token = (
        uuid.UUID(account_tenant).hex + progress.enrollment_ticket
        if progress.enrollment_ticket
        else None
    )
    return RecoveryJob(address, token, now + timedelta(minutes=15), "en-US", account_tenant)


async def recovery_confirm(
    conn: AsyncConnection, env: Env, token: str, *, now: datetime
) -> Confirmed:
    """The emailed link: shows this browser which workspace the account is in and hands it the live one-passkey enrollment ticket (the
    ticket is spent when the passkey is registered). Every way the link can fail is the same message."""
    if not token_shaped(token) or len(token) <= _RECOVERY_TOKEN_TENANT:
        raise _invalid()
    try:
        tenant_id = str(uuid.UUID(token[:_RECOVERY_TOKEN_TENANT]))
    except ValueError:
        raise _invalid() from None
    ticket = token[_RECOVERY_TOKEN_TENANT:]
    async with in_tenant(conn, tenant_id):
        principal = await live_recovery_ticket(conn, ticket, now)
        tenant = await load_tenant(conn, tenant_id)
        if principal is None or tenant is None or tenant.status.value in ("SUSPENDED", "CLOSED"):
            raise _invalid()
        client = await console_client_for(conn, tenant_id)
    return Confirmed(
        request_ref="",
        route="RECOVERY",
        enrollment_ticket=ticket,
        ticket_expires_at=now + timedelta(minutes=15),
        console_client_id=client,
        workspace=tenant.tenant_slug,
        masked_address="",
        invitation=False,
    )


async def workspace_for_email(conn: AsyncConnection, env: Env, *, email: str) -> str | None:
    """The workspace name this address's account lives in, so sign-in can start at the right place from any browser. It reveals nothing that signing in does not: the person still
    needs their own passkey there. Callers rate-limit it hard (it does tell whether an address has an account, which the owners accepted for sign-in only; registration still answers every address
    the same way)."""
    address = canonical_email(email)
    email_hash = await address_hash(address)
    async with in_tenant(conn, env.registration_tenant):
        found = await store.account_for(conn, env.registration_tenant, email_hash)
    if found is None:
        return None
    async with in_tenant(conn, found[0]):
        tenant = await load_tenant(conn, found[0])
    return tenant.tenant_slug if tenant is not None else None


async def initiate(
    conn: AsyncConnection,
    env: Env,
    *,
    country: str,
    journey: str,
    email: str,
    locale: str | None,
    notice_version: str,
    nonce: str | None,
    idempotency_key: str | None,
    now: datetime,
    replaces: str | None = None,
) -> Initiated:
    """Validate, route, and only then store. The caller gets the same answer for a new address, a known one, a flooded one and a held one.

    `replaces` is the request this browser already holds (the person is changing the address, state S3): if it is still open it ends here,
    so its link stops working and its copy of the address is dropped. Anything else it names (unknown, verified, held, ended, malformed) is
    ignored without a word, so it cannot be used to learn whether a request exists."""
    code = country_of(country)
    kind = journey_of(journey)
    address = canonical_email(email)
    tag = locale_of(locale)
    if (
        notice_version != env.notice_version
    ):  # the legal notice the person saw is not the current one: nothing is stored
        raise RegistrationError(
            "NOTICE_VERSION_STALE", "Read the current privacy notice, then continue."
        )
    routing = route_country(env, code)
    if routing.state == "UNAVAILABLE":
        raise RegistrationError(
            "REGION_UNAVAILABLE",
            "Zoiko iD accounts are not available for this country yet. Nothing has been stored.",
        )
    if not registration_open(
        env, code, now
    ):  # registration only in legally approved markets: the registry decides, not the cell table
        where = _contact(code)
        raise RegistrationError(
            "COUNTRY_CAPABILITY_UNAVAILABLE",
            "Account registration is not open for this country yet. Nothing has been stored."
            + (f" You can contact {where}." if where else ""),
        )
    if routing.state == "OTHER_CELL":
        raise RegistrationError("CELL_ROUTING_REQUIRED", routing.url or routing.cell or "")
    email_hash = await address_hash(address)
    risky = not await check_nonce(nonce, now)
    async with in_tenant(conn, env.registration_tenant):
        if (
            idempotency_key
        ):  # a refresh or a double click is the same request, not a second one (AC-08)
            prior = await store.by_idempotency_key(conn, env.registration_tenant, idempotency_key)
            if prior is not None:
                return Initiated(prior.request_ref, None)
        if replaces and request_shaped(replaces):
            earlier = await store.by_ref(conn, replaces, lock=True)
            if earlier is not None and await store.end_open(conn, earlier.intent_id, now):
                await _event(
                    conn,
                    env,
                    "onboarding.registration.abandoned",
                    earlier.intent_id,
                    {"reason": "ADDRESS_CHANGED"},
                )
        known = await store.account_for(conn, env.registration_tenant, email_hash) is not None
        flooded = (
            await store.sent_last_hour(
                conn, env.registration_tenant, email_hash, now - timedelta(hours=1)
            )
            >= MAX_INTENTS_PER_ADDRESS_HOUR
        )
        will_send = not (risky or known or flooded)
        if will_send:
            await store.supersede_open(conn, env.registration_tenant, email_hash, now)
        token, digest = new_token() if will_send else ("", b"")
        ref = new_request_ref()
        state = (
            State.RISK_HOLD if risky else State.VERIFICATION_SENT if will_send else State.INITIATED
        )
        intent_id = await store.insert_intent(
            conn,
            tenant_id=env.registration_tenant,
            request_ref=ref,
            country=code,
            journey=kind,
            locale=tag,
            notice_version=notice_version,
            email_hash=email_hash,
            email_pending=address if will_send else None,
            state=state,
            token_hash=digest if will_send else None,
            token_expires_at=now + TOKEN_TTL if will_send else None,
            sent_at=now if will_send else None,
            idempotency_key=idempotency_key,
            home_cell_id=env.here,
            existing_account=known,
            now=now,
            expires_at=now + INTENT_TTL,
        )
        await _event(
            conn,
            env,
            "onboarding.registration.started",
            intent_id,
            {"country": code, "journey": kind.value, "locale": tag},
        )
        if state is State.RISK_HOLD:
            await _event(
                conn,
                env,
                "onboarding.registration.failed_safe",
                intent_id,
                {"reason": "ABUSE_CONTROL"},
            )
        else:
            await _event(
                conn,
                env,
                "onboarding.registration.email_initiated",
                intent_id,
                {"country": code, "sent": will_send},
            )
    job = SendJob(ref, address, token, now + TOKEN_TTL, tag, 0) if will_send else None
    return Initiated(ref, job)


# -------------------------------------------------------------------------------------------------- resend


async def resend(
    conn: AsyncConnection, env: Env, request_ref: str, *, now: datetime
) -> SendJob | None:
    """A new link for an open request, rarely and never in a burst; the old link stops working. Nothing about the outcome is revealed."""
    if not request_shaped(request_ref):
        return None
    async with in_tenant(conn, env.registration_tenant):
        rec = await store.by_ref(conn, request_ref, lock=True)
        if (
            rec is None
            or rec.existing_account
            or rec.state is State.RISK_HOLD
            or not may_resend(rec.intent, now)
        ):
            return None
        address = rec.email_pending
        if (
            address is None
        ):  # the address was never held (a known or flooded request sent nothing): there is nothing to resend
            return None
        token, digest = new_token()
        await store.mark_sent(
            conn,
            rec.intent_id,
            token_hash=digest,
            token_expires_at=now + TOKEN_TTL,
            now=now,
            resend=True,
        )
        return SendJob(
            rec.request_ref, address, token, now + TOKEN_TTL, rec.locale, rec.resend_count + 1
        )


# ------------------------------------------------------------------------------------------------- confirm


@dataclass(frozen=True, slots=True)
class Confirmed:
    request_ref: str
    route: str  # PERSONAL_WORKSPACE, ORGANIZATION_CLAIM or ACCEPT_INVITATION, after the authenticator is enrolled
    enrollment_ticket: str  # shown once
    ticket_expires_at: datetime
    console_client_id: str | None
    workspace: str | None  # the new personal workspace (never an inviting organization)
    masked_address: str
    invitation: bool
    account: str = "ACTIVE"
    identity: str = "NOT_VERIFIED"


def _invalid(persist: bool = False) -> RegistrationError:
    return RegistrationError(
        "LINK_INVALID", "This link is no longer valid. Request a new link.", persist=persist
    )


async def confirm(
    conn: AsyncConnection, env: Env, request_ref: str | None, token: str, *, now: datetime
) -> Confirmed:
    """Redeem the single-use link. Every reason it can fail is the same message, so a stranger learns nothing about whose request it was."""
    if request_ref is not None and not request_shaped(request_ref):
        raise _invalid()
    if not token_shaped(token):
        raise _invalid()
    async with in_tenant(conn, env.registration_tenant):
        rec = (
            await store.by_ref(conn, request_ref, lock=True)
            if request_ref is not None
            else await store.by_token_hash(conn, token_hash(token), lock=True)
        )
        if rec is None:
            raise _invalid()
        reason = check_token(rec.intent, token, now)
        if reason in ("TOKEN_MISMATCH", "TOKEN_EXPIRED", "TOO_MANY_ATTEMPTS"):
            await store.bump_attempts(conn, rec.intent_id, now)
            raise _invalid(persist=True)
        if reason is not None:
            raise _invalid()
        address = rec.email_pending
        if address is None or rec.existing_account:
            raise _invalid()
        invite = await store.pending_invitation(conn, env.registration_tenant, rec.email_hash)
    if (
        rec.home_cell_id != env.here
    ):  # the request was made for another cell: never create the account in the wrong region
        raise _invalid()
    invitation_tenant: str | None = None
    workspace: str | None = None
    ticket = expires = client = None
    account_tenant = account_principal = ""
    if invite is not None:
        got = await _route_invitation(conn, invite, now)
        if got is not None:
            invitation_tenant, account_principal, ticket, expires = got
            account_tenant = invitation_tenant
            async with in_tenant(conn, invitation_tenant):
                client = await console_client_for(conn, invitation_tenant)
    if ticket is None:
        if env.console is None:
            raise RegistrationError("REGISTRATION_DISABLED", "Sign-up is not enabled here.")
        workspace = f"u-{uuid.uuid4().hex[:20]}"
        try:
            made = await self_signup(
                conn,
                display_name=PERSONAL_WORKSPACE_NAME,
                tenant_slug=workspace,
                deployment_mode="O0",
                home_cell_id=env.here,
                console=env.console,
                registration=None,
                now=now,
            )
        except TenantError as e:
            raise RegistrationError(e.code, e.detail) from e
        account_tenant, account_principal = made.tenant.tenant_id, made.owner_principal_id
        ticket, expires, client = (
            made.enrollment_ticket,
            made.ticket_expires_at,
            made.console_client_id,
        )
        async with in_tenant(
            conn, account_tenant
        ):  # the address moves into the person's own vault; it is never logged
            await set_principal_contact(
                conn, account_tenant, account_principal, address, actor=None
            )
            await set_fields(
                conn, account_tenant, signup_journey=rec.journey.value
            )  # what the dashboard offers first; it grants nothing
    async with in_tenant(conn, env.registration_tenant):
        if not await store.insert_account(
            conn,
            tenant_id=env.registration_tenant,
            email_hash=rec.email_hash,
            account_tenant_id=account_tenant,
            account_principal_id=account_principal,
            country=rec.country,
        ):  # two confirms raced: the first one made the account, this one makes nothing
            raise _invalid()
        if invitation_tenant is not None and invite is not None:
            await store.mark_routed(conn, env.registration_tenant, invite[0], invite[1])
        await store.verified(
            conn,
            rec.intent_id,
            account_tenant_id=account_tenant,
            account_principal_id=account_principal,
            invitation_tenant_id=invitation_tenant,
            now=now,
        )
        await _event(conn, env, "onboarding.registration.verification_completed", rec.intent_id,
                     {"country": rec.country, "journey": rec.journey.value, "invitation": invitation_tenant is not None})  # fmt: skip
    return Confirmed(
        rec.request_ref,
        "ACCEPT_INVITATION"
        if invitation_tenant
        else (
            "ORGANIZATION_CLAIM" if rec.journey is Journey.ORGANIZATION else "PERSONAL_WORKSPACE"
        ),
        str(ticket),
        expires,  # type: ignore[arg-type]
        client,
        workspace,
        mask(address),
        invitation_tenant is not None,
    )


async def _route_invitation(
    conn: AsyncConnection, invite: tuple[str, str, str], now: datetime
) -> tuple[str, str, str, datetime] | None:
    """The invitation this verified address was invited by, if it is still live: a fresh single-use enrollment ticket for the invited
    account, never a self-chosen role (the role was fixed when the invitation was made, and a privileged one was never directly grantable)."""
    inv_tenant, principal, _role = invite
    async with in_tenant(conn, inv_tenant):
        live = [r for r in await roles_of_principal(conn, principal) if r.status.value == "INVITED"]
        tenant = await load_tenant(conn, inv_tenant)
        if not live or tenant is None or tenant.status.value in ("SUSPENDED", "CLOSED"):
            return None
        expires = now + timedelta(hours=23)
        ticket = await issue_enrollment_ticket(
            conn,
            tenant_id=inv_tenant,
            principal_id=principal,
            purpose="BOOTSTRAP",
            issued_by="registration:email-verified",
            expires_at=expires,
        )
    return inv_tenant, principal, ticket, expires


# -------------------------------------------------------------------------------------------------- status


@dataclass(frozen=True, slots=True)
class Status:
    request_ref: str
    state: str
    route: Route
    message: str | None
    expires_at: datetime


async def status(conn: AsyncConnection, env: Env, request_ref: str, *, now: datetime) -> Status:
    if not request_shaped(request_ref):
        raise RegistrationError("REQUEST_NOT_FOUND", "No such request.")
    async with in_tenant(conn, env.registration_tenant):
        rec = await store.by_ref(conn, request_ref, lock=True)
        if rec is None:
            raise RegistrationError("REQUEST_NOT_FOUND", "No such request.")
        state = rec.state
        if state in (State.INITIATED, State.VERIFICATION_SENT) and now >= rec.expires_at:
            await store.sweep_expired(conn, env.registration_tenant, now)
            state = State.EXPIRED
    enrolled = False
    if state is State.VERIFIED and rec.account_tenant_id and rec.account_principal_id:
        async with in_tenant(conn, rec.account_tenant_id):
            enrolled = bool(await passkey_summary(conn, [rec.account_principal_id]))
        if enrolled:
            async with in_tenant(conn, env.registration_tenant):
                await store.set_state(conn, rec.intent_id, State.COMPLETED, now)
                await _event(
                    conn,
                    env,
                    "onboarding.registration.authenticator_enrolled",
                    rec.intent_id,
                    {"country": rec.country},
                )
                await _event(conn, env, "onboarding.registration.completed", rec.intent_id,
                             {"country": rec.country, "journey": rec.journey.value})  # fmt: skip
            state = State.COMPLETED
    elif state is State.COMPLETED:
        enrolled = True
    route = route_for(
        state,
        rec.journey,
        authenticator_enrolled=enrolled,
        invitation=rec.invitation_tenant_id is not None,
    )
    message = {
        State.EXPIRED: "This link is no longer valid. Request a new link.",
        State.RISK_HOLD: "We couldn't complete registration securely. Contact support or try again later.",
    }.get(state)
    if state in (State.INITIATED, State.VERIFICATION_SENT):
        message = GENERIC_MESSAGE
    return Status(rec.request_ref, state.value, route, message, rec.expires_at)


# -------------------------------------------------------------------------------------------------- sweep


async def sweep(conn: AsyncConnection, env: Env, *, now: datetime) -> int:
    """End abandoned requests (the copy of the address and the token are dropped, AC-11) and say so with an aggregate event."""
    async with in_tenant(conn, env.registration_tenant):
        ended = await store.sweep_expired(conn, env.registration_tenant, now)
        for intent_id in ended:
            await _event(conn, env, "onboarding.registration.abandoned", intent_id, {})
    return len(ended)


# ------------------------------------------------------------------------------------ invitation recognition


def invitation_hook(env_tenant: str) -> Callable[..., Any]:
    """The tenant module calls this when an invitation carries an email: only a keyed hash is indexed, so a verified address can be routed to
    its invitation later without any pre-authentication disclosure of who invited it. The address goes to the invitee's own contact vault."""

    async def hook(
        conn: AsyncConnection, tenant_id: str, principal_id: str, role: str, email: str
    ) -> None:
        try:
            address = canonical_email(email)
        except RegistrationError:
            return  # an unusable address is not an invitation index entry; the invitation itself still exists
        h = await address_hash(address)
        await set_principal_contact(conn, tenant_id, principal_id, address, actor=None)
        async with in_tenant(conn, env_tenant):
            await store.index_invitation(
                conn,
                tenant_id=env_tenant,
                email_hash=h,
                invite_tenant_id=tenant_id,
                principal_id=principal_id,
                role=role,
            )

    return hook
