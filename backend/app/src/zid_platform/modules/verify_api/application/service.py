"""Verify API applications and credentials: create, issue, rotate, revoke, exchange for a token, and authenticate a token (CR-149; ZID-EXT-IDV-001 WF-05, SEC-01 to SEC-04).

Nothing here decides who a person is. It decides whether a SERVER holding a client id and a secret may call the API, for which application and environment, with which scopes. The secret is shown once
and kept only as a hash. A token is bound to the application, environment and scopes it was issued for, and ends at once when its credential is revoked or its application is suspended.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verify_api.domain import app_settings
from zid_platform.modules.verify_api.domain import model as m
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
        set(body) - {"redirect_origins", "allowed_workflows", "default_workflow_id", "branding"}
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
    await store.update_application_settings(
        conn,
        application_id,
        origins=origins,
        allowed=allowed,
        default=default,
        branding=branding,
        now=now,
    )
    return await application_detail(conn, application_id, now)


# ------------------------------------------------------------------------------- credentials


async def _new_credential(
    conn: AsyncConnection, tenant_id: str, principal: str, application_id: str, now: datetime
) -> dict[str, Any]:
    client_id = m.new_client_id()
    secret = m.new_secret()
    credential_id = str(uuid.uuid4())
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
) -> dict[str, Any]:
    """A new credential for the application. The secret is in this answer and nowhere else, ever."""
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
    return await _new_credential(conn, tenant_id, principal, application_id, now)


async def rotate_credential(
    conn: AsyncConnection,
    tenant_id: str,
    principal: str,
    application_id: str,
    credential_id: str,
    *,
    overlap: timedelta | None = None,
    now: datetime | None = None,
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
    fresh = await _new_credential(conn, tenant_id, principal, application_id, now)
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
) -> dict[str, Any]:
    """Client credentials: a server presents its client id and secret and gets a short-lived bearer token. One answer for every failure, so nothing says which part was wrong.
    Call inside `store.auth_scope`."""
    now = now or datetime.now(UTC)
    row = await store.credential_for_client(conn, client_id)
    # always compare a hash, even for an unknown client, so the time taken does not tell a real client id from an invented one
    matches = m.secret_matches(
        client_secret, row["secret_hash"] if row else m.hash_secret("no such credential")
    )
    if (
        row is None
        or not matches
        or not _usable(
            row["credential_status"], row["credential_expires_at"], row["application_status"], now
        )
    ):
        raise m.PartnerError("AUTH_INVALID", AUTH_FAILED)
    requested = scope.split() if scope and scope.strip() else None
    granted = m.check_scopes(
        requested, tuple(row["application_scopes"])
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
    conn: AsyncConnection, bearer: str, now: datetime | None = None
) -> VerifyApiCaller:
    """Who a bearer token says is calling, or AUTH_INVALID. A token ends when its own time is up, when its credential is revoked or past its overlap, or when its application is not active.
    Call inside `store.auth_scope`."""
    now = now or datetime.now(UTC)
    if not bearer.startswith(m.TOKEN_PREFIX):
        raise m.PartnerError("AUTH_INVALID", "The access token is not valid.")
    row = await store.token_with_state(conn, m.hash_secret(bearer))
    if (
        row is None
        or row["expires_at"] <= now
        or not _usable(
            row["credential_status"], row["credential_expires_at"], row["application_status"], now
        )
    ):
        raise m.PartnerError("AUTH_INVALID", "The access token is not valid.")
    return VerifyApiCaller(
        tenant_id=str(row["tenant_id"]),
        application_id=str(row["application_id"]),
        credential_id=str(row["credential_id"]),
        environment=row["environment"],
        scopes=tuple(row["scopes"]),
        expires_at=row["expires_at"],
    )
