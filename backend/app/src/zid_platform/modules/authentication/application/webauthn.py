"""Passkey registration, login and step-up (ZID-AUTH-001 §3, §4, §5.1; WebAuthn Level 3; AUTH-W1).

    registration_options / registration_verify     add a passkey (session or enrollment ticket)
    authentication_options / authentication_verify login (discoverable) or step-up (allow-listed)

The RP ID and allowed origins come from configuration, never from the request's Host header. Every
challenge is random, single-use, short-lived and bound to tenant + ceremony (+ principal/session).
A successful login creates a *new* identity session with a fresh handle (no fixation); a step-up
creates a fresh session and revokes the one it replaces, because assurance is fixed at issue.
"""

from __future__ import annotations

import base64
import json
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.exceptions import (
    InvalidAuthenticationResponse,
    InvalidRegistrationResponse,
)
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)
from zid_outbox import emit

from zid_platform.modules.authentication.domain.authenticator import (
    CHALLENGE_TTL,
    STRONG_TYPES,
    Authenticator,
    AuthenticatorStatus,
    AuthnError,
    passkey_assurance,
    sign_count_anomaly,
)
from zid_platform.modules.authentication.infrastructure import authenticator_store as store
from zid_platform.modules.commercial.ports import key_auth, record_active_human, record_billable
from zid_platform.modules.configuration.ports import session_expiry
from zid_platform.modules.principal.ports import PrincipalStatus, load_principal
from zid_platform.modules.session.ports import (
    Session,
    SessionPolicy,
    SessionStatus,
    create_session,
    issue_handle,
    set_session_status,
)

# Algorithm agility (§3.3): EdDSA and ES256 first; RS256 for platform authenticators that only
# offer it. These are authenticator keys, separate from the platform's own JWS policy (DEC-F5).
PUBKEY_ALGS = [
    COSEAlgorithmIdentifier.EDDSA,
    COSEAlgorithmIdentifier.ECDSA_SHA_256,
    COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
]


@dataclass(frozen=True, slots=True)
class WebAuthnSettings:
    rp_id: str
    rp_name: str
    origins: tuple[str, ...]
    session_policy: SessionPolicy = field(default_factory=SessionPolicy)
    # FR-AUT-010 / WebAuthn L3 Related Origin Requests: other Zoiko domains allowed to use this RP
    # ID. Governed configuration only (published at /.well-known/webauthn); https, exact origins.
    related_origins: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.rp_id or not self.origins:
            raise ValueError("WebAuthn needs an explicit RP ID and origin allowlist")
        for o in self.origins:
            if not o.startswith(("https://", "http://localhost")):
                raise ValueError(
                    f"origin {o} must be https (http://localhost only for development)"
                )
        labels = set()
        for o in self.related_origins:
            host = o.removeprefix("https://")
            if not o.startswith("https://") or "/" in host or "*" in host or not host:
                raise ValueError(f"related origin {o} must be an exact https origin")
            labels.add(".".join(host.split(".")[-2:]))
        if len(labels) > 5:  # WebAuthn L3 §5.11: clients honour at most five registrable labels
            raise ValueError("at most five registrable domains may be related origins")

    @property
    def all_origins(self) -> tuple[str, ...]:
        return self.origins + tuple(o for o in self.related_origins if o not in self.origins)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def challenge_of(credential: dict[str, Any]) -> bytes:
    """The challenge the client signed over, read from clientDataJSON (verified again by the lib)."""
    try:
        client_data = json.loads(_unb64(credential["response"]["clientDataJSON"]))
        return _unb64(client_data["challenge"])
    except (KeyError, TypeError, ValueError) as e:
        raise AuthnError("WEBAUTHN_RESPONSE_INVALID", "clientDataJSON") from e


def _raw_id(credential: dict[str, Any]) -> bytes:
    try:
        return _unb64(str(credential["rawId"]))
    except (KeyError, ValueError) as e:
        raise AuthnError("WEBAUTHN_RESPONSE_INVALID", "rawId") from e


async def _principal_active(conn: AsyncConnection, principal_id: str) -> None:
    try:
        principal, _ = await load_principal(conn, principal_id)
    except ValueError as e:
        raise AuthnError("PRINCIPAL_NOT_ACTIVE") from e
    if principal.status is not PrincipalStatus.ACTIVE:
        raise AuthnError("PRINCIPAL_NOT_ACTIVE")


async def registration_options(
    conn: AsyncConnection,
    settings: WebAuthnSettings,
    *,
    tenant_id: str,
    principal_id: str,
    session_id: str | None,
    ticket_hash: bytes | None,
    user_name: str,
    now: datetime,
) -> dict[str, Any]:
    """Caller has already proven the context: an ACTIVE session of `principal_id`, or a live ticket."""
    await _principal_active(conn, principal_id)
    existing = await store.for_principal(conn, principal_id)
    challenge = secrets.token_bytes(32)
    options = generate_registration_options(
        rp_id=settings.rp_id,
        rp_name=settings.rp_name,
        user_id=uuid.UUID(principal_id).bytes,  # opaque handle, no PII (§21)
        user_name=user_name,
        challenge=challenge,
        timeout=int(CHALLENGE_TTL.total_seconds() * 1000),
        attestation=AttestationConveyancePreference.NONE,  # privacy-preserving default (§3.3)
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=a.credential_id)
            for a in existing
            if a.credential_id and a.status is AuthenticatorStatus.ACTIVE
        ],
        supported_pub_key_algs=PUBKEY_ALGS,
    )
    await store.save_challenge(
        conn,
        tenant_id=tenant_id,
        challenge=challenge,
        ceremony="REGISTRATION",
        principal_id=principal_id,
        session_id=session_id,
        ticket_hash=ticket_hash,
        user_verification="required",
        expires_at=now + CHALLENGE_TTL,
    )
    result: dict[str, Any] = json.loads(options_to_json(options))
    return result


@dataclass(frozen=True, slots=True)
class Registered:
    authenticator: Authenticator
    ticket_purpose: str | None
    recovery_id: str | None


async def registration_verify(
    conn: AsyncConnection,
    settings: WebAuthnSettings,
    *,
    tenant_id: str,
    credential: dict[str, Any],
    session_id: str | None,
    label: str | None,
    now: datetime,
) -> Registered:
    challenge = challenge_of(credential)
    claimed = await store.claim_challenge(conn, challenge, ceremonies=("REGISTRATION",), now=now)
    if claimed is None or claimed.principal_id is None:
        raise AuthnError("WEBAUTHN_CHALLENGE_INVALID")
    if claimed.session_id is not None and claimed.session_id != session_id:
        raise AuthnError("WEBAUTHN_CHALLENGE_INVALID", "ceremony started in another session")
    ticket = None
    if claimed.ticket_hash is not None:
        ticket = await store.spend_ticket(conn, claimed.ticket_hash, now)
        if ticket is None or ticket.principal_id != claimed.principal_id:
            raise AuthnError("ENROLLMENT_TICKET_INVALID")
    await _principal_active(conn, claimed.principal_id)
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=settings.rp_id,
            expected_origin=list(settings.all_origins),
            require_user_presence=True,
            require_user_verification=True,
            supported_pub_key_algs=PUBKEY_ALGS,
        )
    except InvalidRegistrationResponse as e:
        raise AuthnError("WEBAUTHN_VERIFICATION_FAILED", str(e)[:200]) from e
    transports = credential.get("response", {}).get("transports") or []
    authenticator = Authenticator(
        authenticator_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        principal_id=claimed.principal_id,
        authenticator_type="PASSKEY",
        status=AuthenticatorStatus.ACTIVE,
        registered_at=now,
        credential_id=verified.credential_id,
        public_key_cose=verified.credential_public_key,
        sign_count=verified.sign_count,
        user_verified=verified.user_verified,
        backup_eligible=verified.credential_device_type == "multi_device",
        backed_up=verified.credential_backed_up,
        transports=tuple(str(t) for t in transports if isinstance(t, str))[:8],
        label=(label or "")[:64] or None,
        aaguid=verified.aaguid or None,
    )
    await store.add_authenticator(conn, authenticator, aal=2)
    return Registered(
        authenticator,
        ticket.purpose if ticket else None,
        ticket.recovery_id if ticket else None,
    )


async def authentication_options(
    conn: AsyncConnection,
    settings: WebAuthnSettings,
    *,
    tenant_id: str,
    step_up_session: Session | None,
    now: datetime,
) -> dict[str, Any]:
    """Discoverable login when no session is given; step-up for the session's principal otherwise."""
    challenge = secrets.token_bytes(32)
    allow: list[PublicKeyCredentialDescriptor] = []
    if step_up_session is not None:
        mine = await store.for_principal(conn, step_up_session.principal_id, active_only=True)
        allow = [
            PublicKeyCredentialDescriptor(id=a.credential_id)
            for a in mine
            if a.credential_id and a.authenticator_type in STRONG_TYPES
        ]
        if not allow:
            raise AuthnError("NO_STRONG_AUTHENTICATOR")
    options = generate_authentication_options(
        rp_id=settings.rp_id,
        challenge=challenge,
        timeout=int(CHALLENGE_TTL.total_seconds() * 1000),
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    await store.save_challenge(
        conn,
        tenant_id=tenant_id,
        challenge=challenge,
        ceremony="STEP_UP" if step_up_session else "AUTHENTICATION",
        principal_id=step_up_session.principal_id if step_up_session else None,
        session_id=step_up_session.session_id if step_up_session else None,
        ticket_hash=None,
        user_verification="required",
        expires_at=now + CHALLENGE_TTL,
    )
    result: dict[str, Any] = json.loads(options_to_json(options))
    return result


@dataclass(frozen=True, slots=True)
class LoggedIn:
    session: Session
    handle: str
    authenticator_id: str
    amr: tuple[str, ...]
    sign_count_anomaly: bool
    replaced_session_id: str | None


async def authentication_verify(
    conn: AsyncConnection,
    settings: WebAuthnSettings,
    *,
    tenant_id: str,
    credential: dict[str, Any],
    now: datetime,
    risk_class: int = 1,
) -> LoggedIn:
    challenge = challenge_of(credential)
    claimed = await store.claim_challenge(
        conn, challenge, ceremonies=("AUTHENTICATION", "STEP_UP"), now=now
    )
    if claimed is None:
        raise AuthnError("WEBAUTHN_CHALLENGE_INVALID")
    authenticator = await store.by_credential(conn, _raw_id(credential))
    if authenticator is None or authenticator.status is not AuthenticatorStatus.ACTIVE:
        raise AuthnError("AUTHENTICATOR_NOT_ACTIVE")
    if claimed.principal_id is not None and claimed.principal_id != authenticator.principal_id:
        raise AuthnError("WEBAUTHN_CHALLENGE_INVALID", "step-up by another principal")
    handle = (credential.get("response") or {}).get("userHandle")
    if handle and _unb64(str(handle)) != uuid.UUID(authenticator.principal_id).bytes:
        raise AuthnError("WEBAUTHN_RESPONSE_INVALID", "userHandle")
    await _principal_active(conn, authenticator.principal_id)
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=settings.rp_id,
            expected_origin=list(settings.all_origins),
            credential_public_key=authenticator.public_key_cose or b"",
            credential_current_sign_count=0,  # counter anomalies are a risk signal, not a lockout
            require_user_verification=claimed.user_verification == "required",
        )
    except InvalidAuthenticationResponse as e:
        raise AuthnError("WEBAUTHN_VERIFICATION_FAILED", str(e)[:200]) from e
    anomaly = sign_count_anomaly(authenticator.sign_count, verified.new_sign_count)
    await store.record_use(
        conn,
        authenticator,
        sign_count=verified.new_sign_count,
        backed_up=verified.credential_backed_up,
        now=now,
    )
    assurance = passkey_assurance(
        user_verified=verified.user_verified,
        backup_eligible=verified.credential_device_type == "multi_device",
        anomaly=anomaly,
    )
    principal, _ = await load_principal(conn, authenticator.principal_id)
    session = Session(
        session_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        principal_id=authenticator.principal_id,
        auth_time=now,
        authentication_context="native:webauthn",
        aal=assurance.aal,
        observed_security_revision=principal.security_revision,
        risk_class=risk_class,
        status=SessionStatus.ACTIVE,
        expires_at=await session_expiry(conn, now, settings.session_policy.absolute_ttl),
        last_seen_at=now,
    )
    await create_session(conn, session)
    await _bill_authentication(conn, tenant_id, session.session_id, authenticator.principal_id, now)
    replaced = None
    if claimed.ceremony == "STEP_UP" and claimed.session_id:
        await set_session_status(conn, claimed.session_id, SessionStatus.REVOKED, reason="rotated")
        replaced = claimed.session_id
    await emit(
        conn,
        name="authentication.succeeded",
        tenant_id=tenant_id,
        aggregate_type="authentication",
        aggregate_id=session.session_id,
        aggregate_version=1,
        data={
            "principal_id": session.principal_id,
            "session_id": session.session_id,
            "authenticator_id": authenticator.authenticator_id,
            "method": "webauthn",
            "amr": list(assurance.amr),
            "aal": assurance.aal,
            "step_up": claimed.ceremony == "STEP_UP",
            "sign_count_anomaly": anomaly,
        },
    )
    return LoggedIn(
        session,
        await issue_handle(conn, session.session_id),
        authenticator.authenticator_id,
        assurance.amr,
        anomaly,
        replaced,
    )


async def record_failure(
    conn: AsyncConnection, *, tenant_id: str, code: str, ceremony: str, now: datetime
) -> None:
    """authentication.failed with a privacy-safe reason only (§21, §23)."""
    await emit(
        conn,
        name="authentication.failed",
        tenant_id=tenant_id,
        aggregate_type="authentication_attempt",
        aggregate_id=str(uuid.uuid4()),
        aggregate_version=1,
        data={"reason": code, "ceremony": ceremony, "at": now.isoformat()},
    )


async def _bill_authentication(
    conn: AsyncConnection, tenant_id: str, session_id: str, principal_id: str, now: datetime
) -> None:
    """One successful authentication completion, and the principal's monthly-active mark (CR-025).

    A step-up on a live session is a new completion too. Never raises: billing cannot block a sign-in."""
    await record_billable(
        conn,
        tenant_id=tenant_id,
        meter="AUTH_EVENT",
        key=key_auth(session_id),
        at=now,
        principal_class="HUMAN",
    )
    await record_active_human(conn, tenant_id=tenant_id, principal_id=principal_id, at=now)
