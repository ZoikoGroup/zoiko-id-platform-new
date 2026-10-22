"""Public Protocol interface of the authentication module. Other modules import ONLY this."""

from datetime import datetime

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
