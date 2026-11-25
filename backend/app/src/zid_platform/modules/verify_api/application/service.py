"""Verify API applications and credentials: create, issue, rotate, revoke, exchange for a token, and authenticate a token (CR-149; ZID-EXT-IDV-001 WF-05, SEC-01 to SEC-04).

Nothing here decides who a person is. It decides whether a SERVER holding a client id and a secret may call the API, for which application and environment, with which scopes. The secret is shown once
and kept only as a hash. A token is bound to the application, environment and scopes it was issued for, and ends at once when its credential is revoked or its application is suspended.
"""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_crypto import CryptoError, verify_external_jws

from zid_platform.modules.verify_api.application import approvals_service as approvals
from zid_platform.modules.verify_api.application import certificates as certs
from zid_platform.modules.verify_api.application import observe
from zid_platform.modules.verify_api.domain import app_settings
from zid_platform.modules.verify_api.domain import approvals as approval_rules
from zid_platform.modules.verify_api.domain import credential_options as opts
from zid_platform.modules.verify_api.domain import model as m
from zid_platform.modules.verify_api.domain import scope_changes
from zid_platform.modules.verify_api.infrastructure import go_live_store, store

AUTH_FAILED = "The client id or secret is not valid."


@dataclass(frozen=True, slots=True)
class VerifyApiCaller:
    """Who a bearer token says is calling: one company's application, in one environment, with these scopes. Never carries a secret."""

    tenant_id: str
    application_id: str
    credential_id: str
    environment: str
    scopes: tuple[str, ...]
    expires_at: datetime

    def require(self, scope: str) -> None:
        if scope not in self.scopes:
            raise m.PartnerError(
                "TENANT_SCOPE_DENIED", f"This token does not carry the scope {scope}."
            )


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def application_view(a: dict[str, Any]) -> dict[str, Any]:
    return {
        "application_id": str(a["application_id"]),
        "name": a["name"],
        "environment": a["environment"],
        "status": a["status"],
        "scopes": list(a["scopes"]),
        "redirect_origins": list(a["redirect_origins"]),
        "allowed_workflows": list(a["allowed_workflows"]),
        "default_workflow_id": a["default_workflow_id"],
        "branding": dict(a["branding"] or {}),
        "ip_allowlist": list(a["ip_allowlist"]),
        "created_at": _iso(a["created_at"]),
        "updated_at": _iso(a["updated_at"]),
    }


def credential_view(c: dict[str, Any], now: datetime) -> dict[str, Any]:
    """What a person may see of a credential: never the secret, only the public client id and the last four characters of the secret."""
    expired = c["status"] != "REVOKED" and c["expires_at"] <= now
    return {
        "credential_id": str(c["credential_id"]),
        "client_id": c["client_id"],
        "secret_hint": c["secret_hint"],
        "auth_method": c["auth_method"],
        "key_id": (c["public_jwk"] or {}).get("kid")
        if c["auth_method"] == "PRIVATE_KEY_JWT"
        else (bytes(c["cert_sha256"]).hex()[:16] if c["auth_method"] == "MTLS" else None),
        "status": "EXPIRED" if expired else c["status"],
        "created_at": _iso(c["created_at"]),
        "expires_at": _iso(c["expires_at"]),
        "last_used_at": _iso(c["last_used_at"]),
        "revoked_at": _iso(c["revoked_at"]),
        "replaced_by": str(c["replaced_by"]) if c["replaced_by"] else None,
    }


# ------------------------------------------------------------------------------- applications


async def create_application(
    conn: AsyncConnection,
    tenant_id: str,
    principal: str,
    *,
    name: str,
    environment: str,
    scopes: list[str] | None,
    origins: list[str] | None,
) -> dict[str, Any]:
    wants_production = environment.upper() == "PRODUCTION"
    env = m.check_environment(
        environment,
        production_open=wants_production and await go_live_store.active_grant(conn) is not None,
    )
    clean_name = m.check_name(name)
    granted = m.check_scopes(scopes)
    allowed = m.check_origins(origins)
    if await store.count_live_applications(conn) >= m.MAX_APPLICATIONS_PER_TENANT:
        raise m.PartnerError(
            "LIMIT_REACHED",
            f"A company can have at most {m.MAX_APPLICATIONS_PER_TENANT} live applications. Retire one first.",
        )
    application_id = str(uuid.uuid4())
    try:
        await store.insert_application(
            conn,
            application_id=application_id,
            tenant_id=tenant_id,
            name=clean_name,
            environment=env.value,
            scopes=granted,
            origins=allowed,
            created_by=principal,
        )
    except Exception as e:  # the unique index on (company, name, environment) while not retired
        if "api_application_name" in str(e):
            raise m.PartnerError(
                "APPLICATION_NAME_TAKEN",
                "You already have a live application with that name in this environment.",
            ) from e
        raise
    created = await store.get_application(conn, application_id)
    assert created is not None
    return application_view(created)


async def list_applications(conn: AsyncConnection) -> list[dict[str, Any]]:
    return [application_view(a) for a in await store.list_applications(conn)]


async def application_detail(
    conn: AsyncConnection, application_id: str, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    a = await store.get_application(conn, application_id)
    if a is None:
        raise m.PartnerError("NOT_FOUND", "No such application.")
    return {
        **application_view(a),
        "credentials": [
            credential_view(c, now) for c in await store.list_credentials(conn, application_id)
        ],
    }


async def set_status(
    conn: AsyncConnection, application_id: str, target: str, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    a = await store.get_application(conn, application_id, lock=True)
    if a is None:
        raise m.PartnerError("NOT_FOUND", "No such application.")
    new = m.check_app_transition(m.AppStatus(a["status"]), m.AppStatus(target))
    await store.set_application_status(conn, application_id, new.value, now)
    if new is not m.AppStatus.ACTIVE:
        await store.revoke_application_tokens(
            conn, application_id, now
        )  # a suspended or retired application stops working at once
    if new is m.AppStatus.RETIRED:
        for c in await store.list_credentials(conn, application_id):
            await store.revoke_credential(conn, str(c["credential_id"]), now)
    return await application_detail(conn, application_id, now)


async def update_settings(
    conn: AsyncConnection,
    application_id: str,
    body: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Change what an application may do and how its hosted page looks (CR-156). Only the fields named are changed; each is checked as at creation. A retired application is not changed."""
    now = now or datetime.now(UTC)
    unknown = sorted(
        set(body)
        - {
            "redirect_origins",
            "allowed_workflows",
            "default_workflow_id",
            "branding",
            "ip_allowlist",
        }
    )
    if unknown:
        raise m.PartnerError("VALIDATION_FAILED", f"Unknown setting: {', '.join(unknown)}.")
    a = await store.get_application(conn, application_id, lock=True)
    if a is None:
        raise m.PartnerError("NOT_FOUND", "No such application.")
    if a["status"] == m.AppStatus.RETIRED.value:
        raise m.PartnerError(
            "APPLICATION_STATE_INVALID", "A retired application cannot be changed."
        )
    origins = (
        list(m.check_origins(body["redirect_origins"]))
        if "redirect_origins" in body
        else list(a["redirect_origins"])
    )
    allowed, default = list(a["allowed_workflows"]), a["default_workflow_id"]
    if "allowed_workflows" in body or "default_workflow_id" in body:
        allowed, default = app_settings.check_workflows(
            body.get("allowed_workflows", allowed),
            body.get("default_workflow_id", default),
        )
    branding = (
        app_settings.check_branding(body["branding"])
        if "branding" in body
        else dict(a["branding"] or {})
    )
    ip_allowlist = (
        opts.check_allowlist(body["ip_allowlist"])
        if "ip_allowlist" in body
        else list(a["ip_allowlist"])
    )
    await store.update_application_settings(
        conn,
        application_id,
        origins=origins,
        allowed=allowed,
        default=default,
        branding=branding,
        ip_allowlist=ip_allowlist,
        now=now,
    )
    return await application_detail(conn, application_id, now)


# ------------------------------------------------------------------------------- credentials


async def _new_credential(
    conn: AsyncConnection,
    tenant_id: str,
    principal: str,
    application_id: str,
    now: datetime,
    public_jwk: dict[str, Any] | None = None,
    certificate: certs.CertFacts | None = None,
) -> dict[str, Any]:
    client_id = m.new_client_id()
    credential_id = str(uuid.uuid4())
    if certificate is not None:
        await store.insert_credential(
            conn,
            credential_id=credential_id,
            application_id=application_id,
            tenant_id=tenant_id,
            client_id=client_id,
            secret_hash=None,
            secret_hint=None,
            created_by=principal,
            # a credential never outlives the certificate it pins
            expires_at=min(now + m.CREDENTIAL_TTL, certificate.not_after),
            cert_sha256=certificate.sha256,
        )
        return {  # nothing secret exists for this kind of credential either: the partner's server proves itself in the TLS handshake
            "credential_id": credential_id,
            "client_id": client_id,
            "auth_method": "MTLS",
            "key_id": certificate.sha256.hex()[:16],
            "certificate_subject": certificate.subject,
            "expires_at": min(now + m.CREDENTIAL_TTL, certificate.not_after).isoformat(),
        }
    if public_jwk is not None:
        key = opts.check_public_jwk(public_jwk)
        await store.insert_credential(
            conn,
            credential_id=credential_id,
            application_id=application_id,
            tenant_id=tenant_id,
            client_id=client_id,
            secret_hash=None,
            secret_hint=None,
            created_by=principal,
            expires_at=now + m.CREDENTIAL_TTL,
            public_jwk=key,
        )
        return {  # no secret exists for this kind of credential: the company holds the private key
            "credential_id": credential_id,
            "client_id": client_id,
            "auth_method": "PRIVATE_KEY_JWT",
            "key_id": key["kid"],
            "expires_at": (now + m.CREDENTIAL_TTL).isoformat(),
        }
    secret = m.new_secret()
    await store.insert_credential(
        conn,
        credential_id=credential_id,
        application_id=application_id,
        tenant_id=tenant_id,
        client_id=client_id,
        secret_hash=m.hash_secret(secret),
        secret_hint=m.secret_hint(secret),
        created_by=principal,
        expires_at=now + m.CREDENTIAL_TTL,
    )
    return {
        "auth_method": "SECRET",
        "credential_id": credential_id,
        "client_id": client_id,
        "client_secret": secret,
        "secret_hint": m.secret_hint(secret),
        "expires_at": (now + m.CREDENTIAL_TTL).isoformat(),
    }


async def issue_credential(
    conn: AsyncConnection,
    tenant_id: str,
    principal: str,
    application_id: str,
    now: datetime | None = None,
    *,
    public_jwk: dict[str, Any] | None = None,
    certificate_pem: str | None = None,
    approval_id: str | None = None,
) -> dict[str, Any]:
    """A new credential for the application. For a PRODUCTION application it is made only with a second administrator's approval, spent here once. A secret credential's secret is in this answer and nowhere else, ever. With a public key the credential is a private-key one and has no secret at all."""
    now = now or datetime.now(UTC)
    a = await store.get_application(conn, application_id, lock=True)
    if a is None:
        raise m.PartnerError("NOT_FOUND", "No such application.")
    if a["status"] != "ACTIVE":
        raise m.PartnerError(
            "APPLICATION_STATE_INVALID", "Credentials can be issued only for an active application."
        )
    if (
        await store.count_live_credentials(conn, application_id, now)
        >= m.MAX_LIVE_CREDENTIALS_PER_APPLICATION
    ):
        raise m.PartnerError(
            "LIMIT_REACHED",
            f"An application can have at most {m.MAX_LIVE_CREDENTIALS_PER_APPLICATION} usable credentials. Revoke one first.",
        )
    if public_jwk is not None and certificate_pem is not None:
        raise m.PartnerError(
            "VALIDATION_FAILED",
            "A credential signs in with a secret, a private key or a certificate: choose one.",
        )
    cert = (
        certs.check_certificate(certificate_pem, now=now) if certificate_pem is not None else None
    )
    if a["environment"] == "PRODUCTION":
        await approvals.spend(
            conn,
            approval_id,
            principal=principal,
            application_id=application_id,
            action=approval_rules.Action.ISSUE,
            credential_id=None,
            key=opts.check_public_jwk(public_jwk) if public_jwk is not None else None,
            digest=cert.sha256 if cert is not None else None,
            now=now,
        )
    return await _new_credential(conn, tenant_id, principal, application_id, now, public_jwk, cert)


async def rotate_credential(
    conn: AsyncConnection,
    tenant_id: str,
    principal: str,
    application_id: str,
    credential_id: str,
    *,
    overlap: timedelta | None = None,
    now: datetime | None = None,
    public_jwk: dict[str, Any] | None = None,
    certificate_pem: str | None = None,
    approval_id: str | None = None,
) -> dict[str, Any]:
    """Give the application a new credential at once and let the old one keep working for a bounded overlap, so a team can deploy the new secret without downtime."""
    now = now or datetime.now(UTC)
    overlap = overlap if overlap is not None else m.DEFAULT_OVERLAP
    if not timedelta(0) < overlap <= m.MAX_OVERLAP:
        raise m.PartnerError(
            "VALIDATION_FAILED", f"The overlap is between a moment and {m.MAX_OVERLAP.days} days."
        )
    a = await store.get_application(conn, application_id, lock=True)
    if a is None:
        raise m.PartnerError("NOT_FOUND", "No such application.")
    if a["status"] != "ACTIVE":
        raise m.PartnerError(
            "APPLICATION_STATE_INVALID",
            "Credentials can be rotated only for an active application.",
        )
    old = await store.get_credential(conn, application_id, credential_id, lock=True)
    if old is None:
        raise m.PartnerError("NOT_FOUND", "No such credential.")
    if old["status"] != "ACTIVE" or old["expires_at"] <= now:
        raise m.PartnerError(
            "CREDENTIAL_STATE_INVALID", "Only an active credential can be rotated."
        )
    if (
        await store.count_live_credentials(conn, application_id, now)
        >= m.MAX_LIVE_CREDENTIALS_PER_APPLICATION
    ):
        raise m.PartnerError(
            "LIMIT_REACHED",
            f"An application can have at most {m.MAX_LIVE_CREDENTIALS_PER_APPLICATION} usable credentials. Revoke one first.",
        )
    kind = old["auth_method"]
    if kind == "PRIVATE_KEY_JWT" and (public_jwk is None or certificate_pem is not None):
        raise m.PartnerError(
            "VALIDATION_FAILED", "Rotating a private-key credential needs the new public key."
        )
    if kind == "MTLS" and (certificate_pem is None or public_jwk is not None):
        raise m.PartnerError(
            "VALIDATION_FAILED", "Rotating a certificate credential needs the new certificate."
        )
    if kind == "SECRET" and (public_jwk is not None or certificate_pem is not None):
        raise m.PartnerError(
            "VALIDATION_FAILED",
            "A secret credential is replaced by another secret. To change how you sign in, issue a new credential of that kind and revoke this one.",
        )
    cert = (
        certs.check_certificate(certificate_pem, now=now) if certificate_pem is not None else None
    )
    if a["environment"] == "PRODUCTION":
        await approvals.spend(
            conn,
            approval_id,
            principal=principal,
            application_id=application_id,
            action=approval_rules.Action.ROTATE,
            credential_id=credential_id,
            key=opts.check_public_jwk(public_jwk) if public_jwk is not None else None,
            digest=cert.sha256 if cert is not None else None,
            now=now,
        )
    fresh = await _new_credential(conn, tenant_id, principal, application_id, now, public_jwk, cert)
    await store.set_rotating(
        conn,
        credential_id,
        expires_at=min(old["expires_at"], now + overlap),
        replaced_by=fresh["credential_id"],
    )
    return {
        **fresh,
        "replaces": credential_id,
        "old_credential_valid_until": min(old["expires_at"], now + overlap).isoformat(),
    }


async def revoke_credential(
    conn: AsyncConnection, application_id: str, credential_id: str, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    c = await store.get_credential(conn, application_id, credential_id, lock=True)
    if c is None:
        raise m.PartnerError("NOT_FOUND", "No such credential.")
    await store.revoke_credential(conn, credential_id, now)
    fresh = await store.get_credential(conn, application_id, credential_id)
    assert fresh is not None
    return credential_view(fresh, now)


# ------------------------------------------------------------------------------- tokens


def _usable(
    credential_status: str, credential_expires_at: datetime, application_status: str, now: datetime
) -> bool:
    return (
        credential_status in ("ACTIVE", "ROTATING")
        and credential_expires_at > now
        and application_status == "ACTIVE"
    )


async def exchange(
    conn: AsyncConnection,
    client_id: str,
    client_secret: str,
    scope: str | None,
    now: datetime | None = None,
    *,
    caller_ip: str | None = None,
) -> dict[str, Any]:
    """Client credentials: a server presents its client id and secret and gets a short-lived bearer token. One answer for every failure, so nothing says which part was wrong.
    Call inside `store.auth_scope`."""
    now = now or datetime.now(UTC)
    row = await store.credential_for_client(conn, client_id)
    # always compare a hash, even for an unknown client, so the time taken does not tell a real client id from an invented one
    stored = row["secret_hash"] if row and row["secret_hash"] is not None else None
    matches = m.secret_matches(client_secret, stored or m.hash_secret("no such credential"))
    if (
        row is None
        or stored is None  # a private-key credential has no secret to present
        or not matches
        or not _usable(
            row["credential_status"], row["credential_expires_at"], row["application_status"], now
        )
    ):
        raise m.PartnerError("AUTH_INVALID", AUTH_FAILED)
    return await _issue_token(conn, row, scope, now, caller_ip)


async def exchange_certificate(
    conn: AsyncConnection,
    client_id: str,
    presented_sha256: bytes | None,
    scope: str | None,
    now: datetime | None = None,
    *,
    caller_ip: str | None = None,
) -> dict[str, Any]:
    """A server proves itself with a client certificate the EDGE vouches for (see `edge_cert`): its fingerprint must be the one pinned on this credential. One answer for every failure, and the fingerprint is
    compared in constant time even for an unknown client. Call inside `store.auth_scope`."""
    now = now or datetime.now(UTC)
    row = await store.credential_for_client(conn, client_id)
    pinned = bytes(row["cert_sha256"]) if row and row["cert_sha256"] is not None else None
    matches = presented_sha256 is not None and hmac.compare_digest(
        presented_sha256, pinned or b"\x00" * 32
    )
    if (
        row is None
        or pinned is None  # not a certificate credential
        or not matches
        or not _usable(
            row["credential_status"], row["credential_expires_at"], row["application_status"], now
        )
    ):
        raise m.PartnerError("AUTH_INVALID", AUTH_FAILED)
    return await _issue_token(conn, row, scope, now, caller_ip)


def assertion_client_id(assertion: str) -> str | None:
    """The client id an assertion claims (its `sub`), read BEFORE the signature is checked, only to find which public key to check it with. Nothing is trusted from it."""
    import base64
    import json

    try:
        payload = json.loads(base64.urlsafe_b64decode(assertion.split(".")[1] + "=="))
    except (ValueError, IndexError):
        return None
    sub = payload.get("sub") if isinstance(payload, dict) else None
    return sub if isinstance(sub, str) and len(sub) <= 64 else None


async def exchange_assertion(
    conn: AsyncConnection,
    assertion: str,
    audiences: list[str],
    scope: str | None,
    now: datetime | None = None,
    *,
    client_id: str | None = None,
    caller_ip: str | None = None,
) -> dict[str, Any]:
    """Private-key JWT (RFC 7523): a server proves itself with an assertion signed by the private key whose public half the company registered. The assertion must name this client and this token endpoint, be
    short-lived, and be unused: its id is remembered until it expires. One answer for every failure. Call inside `store.auth_scope`."""
    now = now or datetime.now(UTC)
    claimed = assertion_client_id(assertion)
    if client_id is not None and claimed is not None and client_id != claimed:
        raise m.PartnerError("AUTH_INVALID", AUTH_FAILED)
    row = await store.credential_for_client(conn, claimed or client_id or "")
    if (
        row is None
        or row["auth_method"] != "PRIVATE_KEY_JWT"
        or not _usable(
            row["credential_status"], row["credential_expires_at"], row["application_status"], now
        )
    ):
        raise m.PartnerError("AUTH_INVALID", AUTH_FAILED)
    cid = claimed or client_id or ""
    try:
        _header, claims = verify_external_jws(
            assertion, keys=[row["public_jwk"]], algorithms=opts.ASSERTION_ALGS
        )
    except CryptoError as e:
        raise m.PartnerError("AUTH_INVALID", AUTH_FAILED) from e
    jti, expires = opts.check_assertion_claims(claims, client_id=cid, audiences=audiences, now=now)
    if not await store.remember_assertion(
        conn, client_id=cid, jti=jti, tenant_id=str(row["tenant_id"]), expires_at=expires, now=now
    ):
        raise m.PartnerError("AUTH_INVALID", AUTH_FAILED)  # used before: a captured copy is useless
    return await _issue_token(conn, row, scope, now, caller_ip)


async def _issue_token(
    conn: AsyncConnection,
    row: dict[str, Any],
    scope: str | None,
    now: datetime,
    caller_ip: str | None,
) -> dict[str, Any]:
    """The credential has proved itself. Now the network it calls from, then a token for the scopes it may have."""
    if not opts.ip_allowed(list(row["ip_allowlist"]), caller_ip):
        raise m.PartnerError(
            "IP_NOT_ALLOWED", "This application may not be called from this address."
        )
    requested = scope.split() if scope and scope.strip() else None
    in_force = await store.scopes_in_force(conn, str(row["application_id"]), now)
    granted = m.check_scopes(
        requested, tuple(in_force if in_force is not None else row["application_scopes"])
    )  # a subset of what the application holds, never more
    token = m.new_token()
    expires = now + m.TOKEN_TTL
    await store.insert_token(
        conn,
        token_hash=m.hash_secret(token),
        credential_id=str(row["credential_id"]),
        application_id=str(row["application_id"]),
        tenant_id=str(row["tenant_id"]),
        environment=row["environment"],
        scopes=granted,
        issued_at=now,
        expires_at=expires,
    )
    await store.touch_credential(conn, str(row["credential_id"]), now)
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": int(m.TOKEN_TTL.total_seconds()),
        "scope": " ".join(granted),
        "environment": row["environment"].lower(),
    }


async def authenticate(
    conn: AsyncConnection,
    bearer: str,
    now: datetime | None = None,
    *,
    caller_ip: str | None = None,
) -> VerifyApiCaller:
    """Who a bearer token says is calling, or AUTH_INVALID. A token ends when its own time is up, when its credential is revoked or past its overlap, or when its application is not active.
    Call inside `store.auth_scope`."""
    now = now or datetime.now(UTC)
    if not bearer.startswith(m.TOKEN_PREFIX):
        raise m.PartnerError("AUTH_INVALID", "The access token is not valid.")
    row = await store.token_with_state(conn, m.hash_secret(bearer), now)
    if row is not None and row["credential_status"] == "REVOKED" and row.get("revoked_at") is not None:
        observe.REVOCATION_TO_REFUSAL.observe(max(0.0, (now - row["revoked_at"]).total_seconds()))  # the measured containment time (CR-173)
    if (
        row is None
        or row["expires_at"] <= now
        or not _usable(
            row["credential_status"], row["credential_expires_at"], row["application_status"], now
        )
    ):
        raise m.PartnerError("AUTH_INVALID", "The access token is not valid.")
    if not opts.ip_allowed(
        list(row["ip_allowlist"]), caller_ip
    ):  # a token used from outside the allowlist is as good as stolen
        raise m.PartnerError(
            "IP_NOT_ALLOWED", "This application may not be called from this address."
        )
    return VerifyApiCaller(
        tenant_id=str(row["tenant_id"]),
        application_id=str(row["application_id"]),
        credential_id=str(row["credential_id"]),
        environment=row["environment"],
        scopes=scope_changes.effective_scopes(row["application_scopes"], row["scopes"], row["current_scopes"]),  # a scope removed after the token was issued is gone on this very call
        expires_at=row["expires_at"],
    )


# ------------------------------------------------------------------------------------------------------------- versioned scopes (CR-173)


async def change_scopes(conn: AsyncConnection, tenant_id: str, application_id: str, proposed_by: str, wanted: list[str], reason: str, effective_at: datetime | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Propose a new scope set. A removal can be staged; an addition to a production application waits for a second person."""
    now = now or datetime.now(UTC)
    a = await store.get_application(conn, application_id, lock=True)
    if a is None:
        raise m.PartnerError("NOT_FOUND", "No such application.")
    if a["status"] == m.AppStatus.RETIRED.value:
        raise m.PartnerError("APPLICATION_STATE_INVALID", "A retired application cannot be changed.")
    current = await store.scopes_in_force(conn, application_id, now)
    base = list(current if current is not None else a["scopes"])
    ch = scope_changes.plan(base, wanted, production=str(a["environment"]) == m.Environment.PRODUCTION.value, now=now, effective_at=effective_at)
    version = await store.next_scope_version(conn, application_id)
    cid = await store.insert_scope_change(conn, tenant_id, application_id, version, ch.kind, ch.resulting_scopes, ch.requires_approval, proposed_by, ch.effective_at, reason)
    return {"change_id": cid, "version": version, "kind": ch.kind, "resulting_scopes": list(ch.resulting_scopes), "effective_at": ch.effective_at.isoformat(), "needs_approval": ch.requires_approval, "approved": not ch.requires_approval}


async def approve_scope_change(conn: AsyncConnection, tenant_id: str, change_id: str, approver: str) -> dict[str, Any]:
    c = await store.get_scope_change(conn, change_id)
    if c is None:
        raise m.PartnerError("NOT_FOUND", "No such scope change.")
    if not c["requires_approval"]:
        raise m.PartnerError("APPROVAL_STATE_INVALID", "This change does not need an approval.")
    problems = scope_changes.approval_problems(str(c["proposed_by"]), approver, list(c["approvers"]))
    if "PROPOSER_CANNOT_APPROVE_OWN_CHANGE" in problems:
        raise m.PartnerError("APPROVAL_SELF_DECISION", "The person who proposed a scope change cannot approve it.")
    if problems:
        raise m.PartnerError("APPROVAL_STATE_INVALID", "You already approved this change.")
    await store.insert_scope_approval(conn, tenant_id, change_id, approver)
    return {"change_id": change_id, "approved": True, "approved_by": approver}


async def rollback_scopes(conn: AsyncConnection, tenant_id: str, application_id: str, proposed_by: str, to_version: int, reason: str, now: datetime | None = None) -> dict[str, Any]:
    """Go back to the scope set of an earlier version (0 is what the application had when it was created). It is a NEW version with the old set, so history is never rewritten, a rollback
    that widens a production application still needs a second person, and no session or idempotency key is touched."""
    a = await store.get_application(conn, application_id)
    if a is None:
        raise m.PartnerError("NOT_FOUND", "No such application.")
    if to_version == 0:
        target = list(a["scopes"])
    else:
        found = next((c for c in await store.scope_changes(conn, application_id) if c["version"] == to_version), None)
        if found is None:
            raise m.PartnerError("NOT_FOUND", "There is no such scope version.")
        target = list(found["resulting_scopes"])
    return await change_scopes(conn, tenant_id, application_id, proposed_by, target, f"rollback to version {to_version}: {reason}"[:300], now=now)
