"""Developer sandbox test identities (plan feature standard.developer-sandbox-test-identities).

A test identity is a HUMAN principal of the company that is never invited and has no passkey, so it can be named in a decision request or given
a grant but never signs in. Each has a label and an expiry; once it expires the worker disables it. At most `MAX_LIVE` live at a time.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.integration.domain.model import IntegrationError
from zid_platform.modules.principal.ports import (
    Principal,
    PrincipalStatus,
    PrincipalType,
    create_principal,
    set_status,
)

MAX_LIVE = 50
MAX_PER_REQUEST = 20
MAX_DAYS = 90


def _view(r: Any) -> dict[str, Any]:
    return {
        "principal_id": str(r.principal_id),
        "label": r.label,
        "created_at": r.created_at.isoformat(),
        "expires_at": r.expires_at.isoformat(),
        "status": "RETIRED"
        if r.retired_at
        else ("EXPIRED" if r.expires_at <= datetime.now(UTC) else "LIVE"),
    }


async def create(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    prefix: str,
    count: int,
    days: int,
    actor: str,
    home_cell: str,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    if not 1 <= count <= MAX_PER_REQUEST or not 1 <= days <= MAX_DAYS:
        raise IntegrationError(
            "SANDBOX_REQUEST_INVALID",
            f"Ask for 1 to {MAX_PER_REQUEST} identities lasting 1 to {MAX_DAYS} days.",
        )
    now = now or datetime.now(UTC)
    live = (
        await conn.execute(
            text("SELECT count(*) FROM platform.sandbox_identity WHERE retired_at IS NULL")
        )
    ).scalar_one()
    if live + count > MAX_LIVE:
        raise IntegrationError(
            "SANDBOX_LIMIT", f"At most {MAX_LIVE} test identities can be live at once; {live} are."
        )
    taken = {
        r[0]
        for r in (
            await conn.execute(
                text("SELECT label FROM platform.sandbox_identity WHERE label LIKE :p"),
                {"p": prefix + "-%"},
            )
        ).all()
    }
    made: list[dict[str, Any]] = []
    n = 0
    while len(made) < count:
        n += 1
        label = f"{prefix}-{n:02d}"
        if label in taken:
            continue
        pid = str(uuid.uuid4())
        await create_principal(
            conn,
            Principal(
                principal_id=pid,
                tenant_id=tenant_id,
                principal_type=PrincipalType.HUMAN,
                status=PrincipalStatus.ACTIVE,
                home_cell=home_cell,
            ),
        )
        await conn.execute(
            text("UPDATE core.principal SET display_label = :l WHERE principal_id = :id"),
            {"l": f"Test identity {label}", "id": uuid.UUID(pid)},
        )
        await conn.execute(
            text(
                "INSERT INTO platform.sandbox_identity (principal_id, tenant_id, label, created_by, expires_at) "
                "VALUES (:id, :t, :l, :a, :e)"
            ),
            {
                "id": uuid.UUID(pid),
                "t": uuid.UUID(tenant_id),
                "l": label,
                "a": uuid.UUID(actor),
                "e": now + timedelta(days=days),
            },
        )
        made.append(
            {
                "principal_id": pid,
                "label": label,
                "expires_at": (now + timedelta(days=days)).isoformat(),
                "status": "LIVE",
                "created_at": now.isoformat(),
            }
        )
    return made


async def listing(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM platform.sandbox_identity ORDER BY created_at DESC, label LIMIT 200"
            )
        )
    ).all()
    return [_view(r) for r in rows]


async def retire(conn: AsyncConnection, principal_id: str) -> None:
    row = (
        await conn.execute(
            text(
                "UPDATE platform.sandbox_identity SET retired_at = now() WHERE principal_id = :id AND retired_at IS NULL RETURNING principal_id"
            ),
            {"id": uuid.UUID(principal_id)},
        )
    ).first()
    if row is None:
        raise IntegrationError("SANDBOX_IDENTITY_NOT_FOUND", "No such live test identity.")
    await set_status(conn, principal_id, PrincipalStatus.DISABLED)


async def expire_due(conn: AsyncConnection, now: datetime) -> int:
    """Worker step: retire every test identity whose time is up. Returns how many."""
    ids = [
        str(r[0])
        for r in (
            await conn.execute(
                text(
                    "SELECT principal_id FROM platform.sandbox_identity WHERE retired_at IS NULL AND expires_at <= :n"
                ),
                {"n": now},
            )
        ).all()
    ]
    for pid in ids:
        await retire(conn, pid)
    return len(ids)
