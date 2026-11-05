"""Public Protocol interface of the authentication module. Other modules import ONLY this."""

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.authentication.application.authenticators import revoke_for_security
from zid_platform.modules.authentication.domain.assurance import (
    AAL,
    MappingProfile,
    effective_level,
    map_assurance,
    step_up_needed,
)
from zid_platform.modules.authentication.infrastructure.link_store import (
    Link,
    find_active,
    for_principal,
)
from zid_platform.modules.authentication.infrastructure.link_store import (
    create_link as create_external_link,
)
from zid_platform.modules.authentication.infrastructure.link_store import (
    revoke as revoke_external_link,
)
from zid_platform.modules.authentication.infrastructure.link_store import (
    touch as touch_external_link,
)

find_external_link = find_active
external_links_for_principal = for_principal


async def require_step_up(
    request: Any, conn: AsyncConnection, caller: Any, **command: Any
) -> dict[str, Any] | None:
    """Fresh strong authentication for a sensitive command (ZID-ENG-DEC-009): call inside the transaction that executes it. Imported on
    first use, because the API layer needs the request machinery, which imports the modules that import these ports."""
    from zid_platform.modules.authentication.api.step_up import require_step_up as guard

    return await guard(request, conn, caller, **command)


def route_step_up_guard() -> Any:
    """The central step-up check (see `api/step_up.py`), handed to the authentication step at start-up. Imported on first use for the same reason as
    `require_step_up`."""
    from zid_platform.modules.authentication.api.step_up import route_step_up_guard as guard

    return guard


async def issue_enrollment_ticket(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    principal_id: str,
    purpose: str,
    issued_by: str,
    expires_at: datetime,
) -> str:
    """A single-use, purpose-bound passkey enrollment ticket (shown once; only its hash is kept).

    Tenant bootstrap and invitations use purpose BOOTSTRAP: the ticket can register one passkey
    for that one principal, nothing else (ZID-AUTH-001 §5; OVERLAY-001 §4)."""
    from zid_platform.modules.authentication.infrastructure.authenticator_store import issue_ticket

    return await issue_ticket(
        conn,
        tenant_id=tenant_id,
        principal_id=principal_id,
        purpose=purpose,
        issued_by=issued_by,
        expires_at=expires_at,
    )


async def passkey_summary(
    conn: AsyncConnection, principal_ids: list[str]
) -> dict[str, dict[str, object]]:
    """Per principal: ACTIVE passkeys, when the first was registered, when one was last used.

    Facts only (no credential ids or keys): what the tenant console shows about an invitee."""
    import uuid

    from sqlalchemy import text

    if not principal_ids:
        return {}
    rows = (
        await conn.execute(
            text(
                "SELECT principal_id, count(*) AS n, min(registered_at) AS first_at, "
                "max(last_used_at) AS used_at FROM authn.authenticator "
                "WHERE status = 'ACTIVE' AND authenticator_type = 'PASSKEY' "
                "AND principal_id = ANY(:p) GROUP BY principal_id"
            ),
            {"p": [uuid.UUID(x) for x in principal_ids]},
        )
    ).all()
    return {
        str(r.principal_id): {"passkeys": int(r.n), "first_at": r.first_at, "used_at": r.used_at}
        for r in rows
    }


__all__ = [
    "AAL",
    "Link",
    "MappingProfile",
    "create_external_link",
    "effective_level",
    "external_links_for_principal",
    "find_external_link",
    "issue_enrollment_ticket",
    "map_assurance",
    "passkey_summary",
    "revoke_external_link",
    "revoke_for_security",
    "step_up_needed",
    "touch_external_link",
]


async def start_email_recovery(
    conn: AsyncConnection, *, tenant_id: str, principal_id: str, now: datetime
) -> Any:
    """Recovery by the account's own email: a `Progress` whose `enrollment_ticket` is set for an ordinary member (one passkey, 15
    minutes) and None for an administrator or lockbox account, whose case keeps its gates; None when one started moments ago."""
    from zid_platform.modules.authentication.application.recovery import start_with_email

    return await start_with_email(conn, tenant_id=tenant_id, principal_id=principal_id, now=now)


async def live_recovery_ticket(conn: AsyncConnection, ticket: str, now: datetime) -> str | None:
    """The principal a live RECOVERY enrollment ticket is for, without spending it (the passkey registration spends it)."""
    from zid_platform.modules.authentication.infrastructure.authenticator_store import peek_ticket

    found = await peek_ticket(conn, ticket, now)
    return found.principal_id if found is not None and found.purpose == "RECOVERY" else None
