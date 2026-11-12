"""Real people for finance roles (ZID-COM-CL-004 C12, section 08): a role that approves is held by an ACTIVE human principal, so a test creates the
principal before it appoints the person. Only the two roles that run work (REVOPS, FINANCE_EXECUTOR) may be held by anything else."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from conftest import ctx
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.modules.commercial.application import finance_service as finance
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import create_principal

# The operator plane's people live in one tenant (the console's own); the value only has to be the same for every call of a test run.
PEOPLE_TENANT = "00000000-0000-4000-8000-0000000000f7"


async def make_principal(
    eng: Any,
    person: str,
    kind: PrincipalType = PrincipalType.HUMAN,
    status: PrincipalStatus = PrincipalStatus.ACTIVE,
) -> None:
    async with tenant_transaction(eng, ctx(PEOPLE_TENANT)) as c:
        known = (
            await c.execute(
                text("SELECT 1 FROM core.principal WHERE principal_id = :p"),
                {"p": uuid.UUID(person)},
            )
        ).first()
        if known is None:
            await create_principal(c, Principal(person, PEOPLE_TENANT, kind, status, "uk-1"))


async def appoint_person(
    eng: Any,
    person: str,
    role: str,
    *,
    start: datetime | None = None,
    kind: PrincipalType = PrincipalType.HUMAN,
    status: PrincipalStatus = PrincipalStatus.ACTIVE,
) -> dict[str, Any]:
    """Create the person (once) and appoint them, as the operator console does: the appointment reads the principal in its own tenant."""
    await make_principal(eng, person, kind, status)
    async with tenant_transaction(eng, ctx(PEOPLE_TENANT)) as c:
        return await finance.appoint(
            c,
            principal=person,
            role=role,
            appointed_by=str(uuid.uuid4()),
            effective_from=start or datetime.now(UTC) - timedelta(days=200),
        )
