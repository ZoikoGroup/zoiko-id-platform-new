"""Country gate for HTTP entry points (ZID-EDR-001 §3: country permissions at every API, job, webhook and action).

One place turns the registry's answer into a refusal, so every route that starts a regulated capability asks the
same question the same way: is this capability open for this country, here, now? Unknown or unapproved is no.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_errors import ZidError

from zid_platform.modules.tenant.ports import declared_registration
from zid_platform.modules.verification.ports import (
    SANDBOX_ENVIRONMENTS,
    Availability,
    Capability,
    CountryError,
    registry,
    require,
)

# The national register a declaration names fixes the country of the entity (never inferred from IP or phone).
NAMESPACE_COUNTRY = {"GB-COH": "GB"}


def environment_of(request: Request) -> str:
    return str(getattr(request.app.state, "environment", "production"))


def enforce(request: Request, country: str, capability: Capability) -> Availability:
    """Raise a 422 `COUNTRY_CAPABILITY_UNAVAILABLE` unless the capability is open for the country."""
    try:
        return require(
            registry(),
            country,
            capability,
            now=datetime.now(UTC),
            environment=environment_of(request),
        )
    except CountryError as e:
        raise ZidError(e.code, str(e), http_status=422) from e


def namespace_country(namespace: str) -> str:
    country = NAMESPACE_COUNTRY.get(namespace)
    if country is None:
        raise ZidError(
            "ORGANIZATION_SOURCE_UNSUPPORTED",
            f"{namespace} is not a supported register.",
            http_status=422,
        )
    return country


async def enforce_tenant(
    request: Request, conn: AsyncConnection, tenant_id: str, capability: Capability
) -> None:
    """The same gate for a tenant's own action: its country is the one its declared register names.

    A deployed environment never acts for a tenant whose country is unknown; local and test may, because their
    tenants are built directly. `conn` is scoped to the tenant."""
    declared = await declared_registration(conn, tenant_id)
    country = NAMESPACE_COUNTRY.get(declared.register_namespace) if declared else None
    if country is None:
        if environment_of(request) in SANDBOX_ENVIRONMENTS:
            return
        raise ZidError(
            "COUNTRY_NOT_DECLARED",
            "The organization's country is not known, so this cannot start here.",
            http_status=422,
        )
    enforce(request, country, capability)
