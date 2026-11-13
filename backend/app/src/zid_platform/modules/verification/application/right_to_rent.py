"""England Right to Rent decision from the platform's records (ZID-RCG-003 section 10.2; CR-084).

The provider's standing for Right to Rent is never assumed; it is read, as of now, from:
- official-register attestations (CR-073), scheme `UK_DIATF` for the certified services, `UK_DVS_REGISTER` for the register listing and
  `UK_DIATF_SUPPLEMENTARY` for the Right to Rent supplementary code, each for the exact service `RTR` in GB, fresh and current;
- the provider entitlement (CR-074) for country GB, capability RIGHT_TO_RENT, purpose STANDARD;
- the kill switch (CR-078) for the provider's RTR service in GB.
`decide_for` is what Zoiko Rooms (or an operator preview) asks before any Right to Rent evidence is produced.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.application import suspensions
from zid_platform.modules.verification.domain import right_to_rent as rtr
from zid_platform.modules.verification.domain import suspension as sp
from zid_platform.modules.verification.domain.attestation import Scope
from zid_platform.modules.verification.domain.entitlement import Scope as EntitlementScope
from zid_platform.modules.verification.infrastructure import attestation_store
from zid_platform.modules.verification.infrastructure import (
    contract_entitlement_store as entitlement_store,
)

CERTIFICATION_SCHEME = "UK_DIATF"
REGISTER_SCHEME = "UK_DVS_REGISTER"
SUPPLEMENTARY_SCHEME = "UK_DIATF_SUPPLEMENTARY"
CAPABILITY = "RIGHT_TO_RENT"


async def provider_status(
    conn: AsyncConnection, provider: str, *, at: datetime
) -> rtr.ProviderStatus:
    certified: set[str] = set()
    for scope in await attestation_store.scopes_of(conn, entity=provider):
        if (
            scope.scheme == CERTIFICATION_SCHEME
            and scope.country == "GB"
            and (await attestation_store.decide_scope(conn, scope, at=at)).certified
        ):
            certified.add(scope.service_code)

    async def holds(scheme: str) -> bool:
        return (
            await attestation_store.decide_scope(
                conn, Scope(provider, scheme, "GB", rtr.RTR_SERVICE), at=at
            )
        ).certified

    entitled = (
        await entitlement_store.decide_scope(
            conn, EntitlementScope(provider, "GB", CAPABILITY, "STANDARD"), at=at
        )
    ).entitled
    stopped = await suspensions.blocking_for(
        conn,
        sp.Invocation(
            provider=provider, service_id=rtr.RTR_SERVICE, country="GB", purpose="STANDARD"
        ),
        at=at,
    )
    return rtr.ProviderStatus(
        provider=provider,
        certified_services=frozenset(certified),
        registered_for_rtr=await holds(REGISTER_SCHEME),
        supplementary_code_valid=await holds(SUPPLEMENTARY_SCHEME),
        entitled=entitled,
        suspended=bool(stopped),
    )


async def decide_for(
    conn: AsyncConnection,
    *,
    property_country: str,
    property_subdivision: str | None,
    route: rtr.Route,
    provider: str | None,
    at: datetime,
    retention: str | None = None,
) -> rtr.Decision:
    status = None
    if (
        route is rtr.Route.DIGITAL_IDSP
        and provider
        and rtr.applies(property_subdivision, property_country)
    ):
        status = await provider_status(conn, provider, at=at)
    return rtr.decide(
        property_country=property_country,
        property_subdivision=property_subdivision,
        route=route,
        on=at.date(),
        provider=status,
        retention=retention,
    )
