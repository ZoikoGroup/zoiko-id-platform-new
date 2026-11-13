"""What enforce would refuse, capability by country, and where each service offering may be shown as available (CR-086, CR-088).

One implementation for the enforce-readiness report, the operator offerings view and the public offerings catalog, so they can never disagree
with each other or with the gates: the same policy-pack, market-release, entitlement, kill-switch and privacy rules the regulated entry points apply.
Platform records only: the caller passes a connection that can read them (a tenant transaction or a plain one).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.application import (
    market_releases,
    policy_packs,
    suspensions,
)
from zid_platform.modules.verification.domain import conformance as cf
from zid_platform.modules.verification.domain import entitlement as em
from zid_platform.modules.verification.domain import market_release as rm
from zid_platform.modules.verification.domain import offerings as of
from zid_platform.modules.verification.domain import privacy_overlay as po
from zid_platform.modules.verification.domain import suspension as sp
from zid_platform.modules.verification.domain.countries import Capability, Registry
from zid_platform.modules.verification.domain.policy_packs import PACK_PURPOSE, Outcome
from zid_platform.modules.verification.infrastructure import conformance_store
from zid_platform.modules.verification.infrastructure import (
    contract_entitlement_store as entitlements,
)

REGULATED = (
    Capability.INDIVIDUAL_VERIFICATION,
    Capability.BUSINESS_VERIFICATION,
    Capability.CREDENTIAL_ISSUANCE,
    Capability.ENTERPRISE_USE,
)


async def capability_refusals(
    conn: AsyncConnection,
    *,
    country: str,
    capability: Capability,
    provider: str | None,
    overlays: Mapping[str, po.Overlay],
    now: datetime,
) -> list[str]:
    """Every refusal enforce would make for this capability in this country now, each prefixed with the check that makes it."""
    name = capability.value
    refuse: list[str] = []
    pack = await policy_packs.governing_pack(
        conn, country=country, product=name, purpose=PACK_PURPOSE, at=now
    )
    if pack.outcome is not Outcome.APPLICABLE:
        refuse.append(f"pack:{pack.reason}")
    market = await market_releases.market_open(
        conn, rm.MarketKey(country, name, PACK_PURPOSE), now=now
    )
    if not market.open:
        refuse.append(f"release:{market.reason}")
    if provider is None:
        refuse.append("entitlement:no_provider")
    else:
        ent = await entitlements.decide_scope(
            conn, em.Scope(provider, country, name, PACK_PURPOSE), at=now
        )
        if not ent.entitled:
            refuse.append(f"entitlement:{ent.reason}")
        stopped = await suspensions.blocking_for(
            conn,
            sp.Invocation(
                provider=provider, service_id=name, country=country, purpose=PACK_PURPOSE
            ),
            at=now,
        )
        refuse.extend(f"suspended:{s.suspension_id}" for s in stopped)
    if capability is Capability.INDIVIDUAL_VERIFICATION:
        route = po.route(overlays.get(country), provider=provider, biometric=False, consent=None)
        refuse.extend(f"privacy:{r}" for r in route.reasons)
    return refuse


async def country_refusals(
    conn: AsyncConnection,
    registry: Registry,
    country: str,
    *,
    overlays: Mapping[str, po.Overlay],
    now: datetime,
) -> dict[str, list[str]]:
    """capability -> refusals, for every regulated capability the country registry lists for this country."""
    entry = registry.countries.get(country)
    if entry is None:
        return {}
    out: dict[str, list[str]] = {}
    for capability in REGULATED:
        cap = entry.capabilities.get(capability)
        if cap is None:
            continue
        out[capability.value] = await capability_refusals(
            conn,
            country=country,
            capability=capability,
            provider=cap.provider,
            overlays=overlays,
            now=now,
        )
    return out


async def accepted_results(conn: AsyncConnection) -> dict[str, str] | None:
    """case -> outcome of the newest conformance run an independent reviewer accepted and nobody rejected; None when there is none."""
    for run in await conformance_store.list_runs(conn, skip_unreadable=True):
        reviews = await conformance_store.reviews(conn, run.run_id)
        if any(r.decision is cf.Decision.REJECTED for r in reviews):
            continue
        if any(r.decision is cf.Decision.ACCEPTED for r in reviews):
            return {r.case: r.outcome.value for r in run.results}
    return None


def privacy_of(overlays: Mapping[str, po.Overlay], country: str) -> of.Privacy | None:
    o = overlays.get(country)
    if o is None:
        return None
    return of.Privacy(usable=not po.problems(o), biometrics=o.biometrics.value)


async def offerings_coverage(
    conn: AsyncConnection,
    catalog: Iterable[of.Offering],
    registry: Registry,
    *,
    overlays: Mapping[str, po.Overlay],
    now: datetime,
    countries: Iterable[str] | None = None,
) -> list[of.Coverage]:
    """Every offering in every country (or the countries given), decided by the publish-coverage rule."""
    run = await accepted_results(conn)
    out: list[of.Coverage] = []
    items = list(catalog)
    for country in sorted(countries if countries is not None else registry.countries):
        if country not in registry.countries:
            continue  # a country the registry does not list has no coverage at all
        refusals = await country_refusals(conn, registry, country, overlays=overlays, now=now)
        privacy = privacy_of(overlays, country)
        out.extend(
            of.decide(o, country, capability_refusals=refusals, privacy=privacy, accepted_run=run)
            for o in items
        )
    return out
