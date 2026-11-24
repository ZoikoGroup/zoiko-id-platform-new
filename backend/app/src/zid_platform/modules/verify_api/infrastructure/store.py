"""Verify API applications, credentials and access tokens in PostgreSQL (CR-149). Tenant rows are scoped by row-level security.

A server calling the API has no signed-in person, so the token exchange and the bearer check look rows up across companies by an unguessable key (a client id, or the hash of a token). They do that only
inside `auth_scope`, a second policy that opens for the enclosed statements, exactly as the commercial operator plane does for its own cross-company reads."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_APP = (
    "application_id, tenant_id, name, environment, status, scopes, redirect_origins, created_by, created_at, updated_at, "
    "allowed_workflows, default_workflow_id, branding"
)
_CRED = "credential_id, application_id, tenant_id, client_id, secret_hint, status, created_by, created_at, expires_at, last_used_at, revoked_at, replaced_by"


@asynccontextmanager
async def auth_scope(conn: AsyncConnection) -> AsyncIterator[None]:
    """Let the token endpoint and the bearer check read credentials and tokens by their own key, for the enclosed statements only."""
    await conn.execute(text("SELECT set_config('app.verify_api_scope', 'auth', true)"))
    try:
        yield
    finally:
        await conn.execute(text("SELECT set_config('app.verify_api_scope', '', true)"))


def _row(r: Any) -> dict[str, Any]:
    return dict(r._mapping)


# ------------------------------------------------------------------------------- applications


async def insert_application(
    conn: AsyncConnection,
    *,
    application_id: str,
    tenant_id: str,
    name: str,
    environment: str,
    scopes: tuple[str, ...],
    origins: tuple[str, ...],
    created_by: str,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO verify.api_application (application_id, tenant_id, name, environment, scopes, redirect_origins, created_by) "
            "VALUES (:i, :t, :n, :e, :s, :o, :by)"
        ),
        {
            "i": uuid.UUID(application_id),
            "t": uuid.UUID(tenant_id),
            "n": name,
            "e": environment,
            "s": list(scopes),
            "o": list(origins),
            "by": uuid.UUID(created_by),
        },
    )


async def count_live_applications(conn: AsyncConnection) -> int:
    return int(
        (
            await conn.execute(
                text("SELECT count(*) FROM verify.api_application WHERE status <> 'RETIRED'")
            )
        ).scalar_one()
    )


async def list_applications(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(f"SELECT {_APP} FROM verify.api_application ORDER BY created_at DESC")
        )
    ).all()
    return [_row(r) for r in rows]


async def get_application(
    conn: AsyncConnection, application_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_APP} FROM verify.api_application WHERE application_id = :i"
                + (" FOR UPDATE" if lock else "")
            ),
            {"i": uuid.UUID(application_id)},
        )
    ).first()
    return _row(row) if row else None


async def set_application_status(
    conn: AsyncConnection, application_id: str, status: str, now: datetime
) -> None:
    await conn.execute(
        text(
            "UPDATE verify.api_application SET status = :s, updated_at = :n WHERE application_id = :i"
        ),
        {"i": uuid.UUID(application_id), "s": status, "n": now},
    )


async def update_application_settings(
    conn: AsyncConnection,
    application_id: str,
    *,
    origins: list[str],
    allowed: list[str],
    default: str | None,
    branding: dict[str, str],
    now: datetime,
) -> None:
    await conn.execute(
        text(
            "UPDATE verify.api_application SET redirect_origins = :o, allowed_workflows = :w, default_workflow_id = :d, "
            "branding = CAST(:b AS jsonb), updated_at = :n WHERE application_id = :i"
        ),
        {
            "i": uuid.UUID(application_id),
            "o": origins,
            "w": allowed,
            "d": default,
            "b": json.dumps(branding),
            "n": now,
        },
    )


# ------------------------------------------------------------------------------- credentials


async def insert_credential(
    conn: AsyncConnection,
    *,
    credential_id: str,
    application_id: str,
    tenant_id: str,
    client_id: str,
    secret_hash: bytes,
    secret_hint: str,
    created_by: str,
    expires_at: datetime,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO verify.api_credential (credential_id, application_id, tenant_id, client_id, secret_hash, secret_hint, status, created_by, expires_at) "
            "VALUES (:i, :a, :t, :c, :h, :hint, 'ACTIVE', :by, :x)"
        ),
        {
            "i": uuid.UUID(credential_id),
            "a": uuid.UUID(application_id),
            "t": uuid.UUID(tenant_id),
            "c": client_id,
            "h": secret_hash,
            "hint": secret_hint,
            "by": uuid.UUID(created_by),
            "x": expires_at,
        },
    )


async def list_credentials(conn: AsyncConnection, application_id: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                f"SELECT {_CRED} FROM verify.api_credential WHERE application_id = :a ORDER BY created_at DESC"
            ),
            {"a": uuid.UUID(application_id)},
        )
    ).all()
    return [_row(r) for r in rows]


async def count_live_credentials(conn: AsyncConnection, application_id: str, now: datetime) -> int:
    """Credentials that can still be used: active, or rotating and not yet past their overlap."""
    return int(
        (
            await conn.execute(
                text(
                    "SELECT count(*) FROM verify.api_credential WHERE application_id = :a AND status <> 'REVOKED' AND expires_at > :n"
                ),
                {"a": uuid.UUID(application_id), "n": now},
            )
        ).scalar_one()
    )


async def get_credential(
    conn: AsyncConnection, application_id: str, credential_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_CRED} FROM verify.api_credential WHERE application_id = :a AND credential_id = :c"
                + (" FOR UPDATE" if lock else "")
            ),
            {"a": uuid.UUID(application_id), "c": uuid.UUID(credential_id)},
        )
    ).first()
    return _row(row) if row else None


async def set_rotating(
    conn: AsyncConnection, credential_id: str, *, expires_at: datetime, replaced_by: str
) -> None:
    await conn.execute(
        text(
            "UPDATE verify.api_credential SET status = 'ROTATING', expires_at = :x, replaced_by = :r WHERE credential_id = :c AND status = 'ACTIVE'"
        ),
        {"c": uuid.UUID(credential_id), "x": expires_at, "r": uuid.UUID(replaced_by)},
    )


async def revoke_credential(conn: AsyncConnection, credential_id: str, now: datetime) -> None:
    await conn.execute(
        text(
            "UPDATE verify.api_credential SET status = 'REVOKED', revoked_at = :n WHERE credential_id = :c AND status <> 'REVOKED'"
        ),
        {"c": uuid.UUID(credential_id), "n": now},
    )
    # every token already issued from it stops working at once
    await conn.execute(
        text(
            "UPDATE verify.api_access_token SET expires_at = :n WHERE credential_id = :c AND expires_at > :n"
        ),
        {"c": uuid.UUID(credential_id), "n": now},
    )


async def revoke_application_tokens(
    conn: AsyncConnection, application_id: str, now: datetime
) -> None:
    await conn.execute(
        text(
            "UPDATE verify.api_access_token SET expires_at = :n WHERE application_id = :a AND expires_at > :n"
        ),
        {"a": uuid.UUID(application_id), "n": now},
    )


async def credential_for_client(conn: AsyncConnection, client_id: str) -> dict[str, Any] | None:
    """Inside `auth_scope`: the credential and its application's state, by the public client id. Includes the secret hash, which never leaves this module's service."""
    row = (
        await conn.execute(
            text(
                "SELECT c.credential_id, c.application_id, c.tenant_id, c.secret_hash, c.status AS credential_status, c.expires_at AS credential_expires_at, "
                "a.status AS application_status, a.environment, a.scopes AS application_scopes "
                "FROM verify.api_credential c JOIN verify.api_application a USING (application_id) WHERE c.client_id = :c"
            ),
            {"c": client_id},
        )
    ).first()
    return _row(row) if row else None


async def touch_credential(conn: AsyncConnection, credential_id: str, now: datetime) -> None:
    await conn.execute(
        text("UPDATE verify.api_credential SET last_used_at = :n WHERE credential_id = :c"),
        {"c": uuid.UUID(credential_id), "n": now},
    )


# ------------------------------------------------------------------------------- tokens


async def insert_token(
    conn: AsyncConnection,
    *,
    token_hash: bytes,
    credential_id: str,
    application_id: str,
    tenant_id: str,
    environment: str,
    scopes: tuple[str, ...],
    issued_at: datetime,
    expires_at: datetime,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO verify.api_access_token (token_hash, credential_id, application_id, tenant_id, environment, scopes, issued_at, expires_at) "
            "VALUES (:h, :c, :a, :t, :e, :s, :i, :x)"
        ),
        {
            "h": token_hash,
            "c": uuid.UUID(credential_id),
            "a": uuid.UUID(application_id),
            "t": uuid.UUID(tenant_id),
            "e": environment,
            "s": list(scopes),
            "i": issued_at,
            "x": expires_at,
        },
    )


async def token_with_state(conn: AsyncConnection, token_hash: bytes) -> dict[str, Any] | None:
    """Inside `auth_scope`: a token with the current state of the credential and application it came from, so a revoked credential or a suspended application ends its tokens at once."""
    row = (
        await conn.execute(
            text(
                "SELECT t.credential_id, t.application_id, t.tenant_id, t.environment, t.scopes, t.expires_at, "
                "c.status AS credential_status, c.expires_at AS credential_expires_at, a.status AS application_status "
                "FROM verify.api_access_token t JOIN verify.api_credential c USING (credential_id) JOIN verify.api_application a ON a.application_id = t.application_id "
                "WHERE t.token_hash = :h"
            ),
            {"h": token_hash},
        )
    ).first()
    return _row(row) if row else None
