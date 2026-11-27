"""Country gate for HTTP entry points (ZID-EDR-001 §3: country permissions at every API, job, webhook and action).

One place turns the registry's answer into a refusal, so every route that starts a regulated capability asks the
same question the same way: is this capability open for this country, here, now? Unknown or unapproved is no.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from zid_context import RequestContext
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import request_engine
from zid_platform.modules.tenant.ports import declared_registration
from zid_platform.modules.verification.ports import (
    GATE_MODES,
    PACK_PURPOSE,
    SANDBOX_ENVIRONMENTS,
    Availability,
    Capability,
    CountryError,
    PolicyPackError,
    availability,
    entitlement_model,
    entitlements,
    gate_decision,
    market_releases,
    pack_hash,
    pack_needed,
    policy_packs,
    registry,
    release_model,
    require,
    suspension_model,
    suspensions,
)

MarketDecision = release_model.MarketDecision
MarketKey = release_model.MarketKey
ReleaseError = release_model.ReleaseError
EntitlementError = entitlement_model.EntitlementError

_LOG = logging.getLogger("zid.regulatory")

# The national register a declaration names fixes the country of the entity (never inferred from IP or phone).
NAMESPACE_COUNTRY = {"GB-COH": "GB"}


def environment_of(request: Request) -> str:
    return str(getattr(request.app.state, "environment", "production"))


def enforce(request: Request, country: str, capability: Capability) -> Availability:
    """Raise a 422 `COUNTRY_CAPABILITY_UNAVAILABLE` unless the capability is open for the country.
    With ZID_IDV_OPEN_TO_ALL_COUNTRIES on, identity checks (INDIVIDUAL_VERIFICATION) are not held by the country registry at all: any signed-in person can complete one wherever they live."""
    if capability is Capability.INDIVIDUAL_VERIFICATION and getattr(
        request.app.state, "idv_open_to_all_countries", False
    ):
        return Availability.ENABLED
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


async def check_packs(
    engine: AsyncEngine,
    ctx: RequestContext | None,
    *,
    mode: str,
    environment: str,
    reg: Any,
    country: str,
    capability: Capability,
    now: datetime,
    release_mode: str = "off",
    entitlement_mode: str = "off",
) -> dict[str, Any] | None:
    """Returns the regulatory basis the request went on (ZID-RCG-003 09.1: the pack id, version and hash, the release, the provider and its
    entitlement, and the switch modes), or None when no regulatory check applies (a capability that is not ENABLED).

    ZID-RCG-003 T01/T02: besides being ENABLED in the country registry, a regulated capability needs an approved policy pack for the
    market, or it is refused before any provider is called (enforce) or only reported (shadow). The registry stays the baseline: a capability
    that is not ENABLED there is decided by `enforce`, and no pack is read for it. The product is the capability, the purpose the standard one.
    A pack that no longer matches its stored hash counts as no pack. Nothing here ever turns into a pass.

    `release_mode` adds RCG-G10: the market also needs a LAUNCHED market release that still covers the packs in force and whose pinned supplier
    certifications are certified on fresh evidence (`market_releases.market_open`). It is read in the same transaction and decided after the
    pack, with the same off, shadow and enforce meaning. A release that cannot be read counts as not released.

    `entitlement_mode` adds RCG-G03: the provider the registry names for the capability must hold a GRANTED entitlement for this exact country,
    capability and purpose (`provider_entitlement`), or the request is refused with `PROVIDER_NOT_ENTITLED` before the provider is called."""
    for name, value in (
        ("market release", release_mode),
        ("provider entitlement", entitlement_mode),
    ):
        if value not in GATE_MODES:
            raise ValueError(f"unknown {name} mode {value!r}")
    state = availability(reg, country, capability, now=now, environment=environment)
    need_pack = pack_needed(mode, state)
    need_release = release_mode != "off" and state is Availability.ENABLED
    need_entitlement = entitlement_mode != "off" and state is Availability.ENABLED
    provider = _provider_of(reg, country, capability)
    # RCG-G08: the kill switch has no off or shadow mode. It costs one read and only ever fires on a suspension someone applied.
    need_suspension = state is Availability.ENABLED and provider is not None
    if not (need_pack or need_release or need_entitlement or need_suspension):
        return None
    stopped: list[Any] = []
    resolution = None
    failed = ""
    market_state: MarketDecision | None = None
    release_failed = ""
    entitled: Any = None
    entitlement_failed = ""
    try:
        async with _read(engine, ctx) as conn:
            if need_suspension and provider:
                stopped = await suspensions.blocking_for(
                    conn,
                    suspension_model.Invocation(
                        provider=provider,
                        service_id=capability.value,
                        country=country.upper(),
                        purpose=PACK_PURPOSE,
                    ),
                    at=now,
                )
            if need_pack:
                try:
                    resolution = await policy_packs.governing_pack(
                        conn,
                        country=country,
                        product=capability.value,
                        purpose=PACK_PURPOSE,
                        at=now,
                    )
                except PolicyPackError as e:
                    failed = str(e)
            if need_release:
                try:
                    market_state = await market_releases.market_open(
                        conn, MarketKey(country, capability.value, PACK_PURPOSE), now=now
                    )
                except (ReleaseError, PolicyPackError) as e:
                    release_failed = str(e)
            if need_entitlement and provider:
                try:
                    entitled = await entitlements.decide_scope(
                        conn,
                        entitlement_model.Scope(provider, country, capability.value, PACK_PURPOSE),
                        at=now,
                    )
                except EntitlementError as e:
                    entitlement_failed = str(e)
    except PolicyPackError as e:  # the transaction itself failed on a pack
        failed = str(e)
    if stopped:
        _LOG.warning(
            "provider suspension refused: %s %s in %s (%s)",
            provider,
            capability.value,
            country,
            ",".join(s.suspension_id for s in stopped),
        )
        raise ZidError(
            "PROVIDER_SCOPE_SUSPENDED",
            f"{capability.value} in {country} is suspended with its provider.",
            http_status=422,
        )
    decision = gate_decision(mode, state, resolution)
    if decision.would_refuse:
        _LOG.warning(
            "regulatory pack check %s: %s %s in %s (%s)",
            "refused" if not decision.allowed else "would refuse (shadow)",
            decision.code,
            capability.value,
            country,
            failed or decision.reason,
        )
    if not decision.allowed:
        raise ZidError(
            decision.code or "REGULATORY_POLICY_UNAVAILABLE",
            (
                f"{capability.value} in {country} needs a manual jurisdiction or policy review."
                if decision.code == "REGULATORY_REVIEW_REQUIRED"
                else f"No approved policy pack covers {capability.value} in {country}."
            ),
            http_status=422,
        )
    if need_release:
        _release_decision(release_mode, capability, country, market_state, release_failed)
    if need_entitlement:
        _entitlement_decision(
            entitlement_mode, capability, country, provider, entitled, entitlement_failed
        )
    pack = resolution.pack if resolution is not None else None
    release = market_state.release if market_state is not None else None
    return {
        "country": country.upper(),
        "capability": capability.value,
        "purpose": PACK_PURPOSE,
        "checked_at": now.isoformat(),
        "modes": {"pack": mode, "release": release_mode, "entitlement": entitlement_mode},
        "pack": None
        if pack is None
        else {"pack_id": pack.pack_id, "version": pack.version, "pack_hash": pack_hash(pack)},
        "pack_outcome": decision.reason,
        "release": None
        if release is None
        else {
            "release_id": release.release_id,
            "version": release.version,
            "release_hash": release.release_hash,
        },
        "market_outcome": (market_state.reason if market_state else (release_failed or None)),
        "provider": provider,
        "entitlement_outcome": (
            entitled.reason if entitled is not None else (entitlement_failed or None)
        ),
        "suspensions_checked": need_suspension,
    }


def _provider_of(reg: Any, country: str, capability: Capability) -> str | None:
    """The provider the registry names for this capability in this country, if any."""
    c = reg.countries.get(country.upper())
    entry = c.capabilities.get(capability) if c else None
    return entry.provider if entry else None


def _entitlement_decision(
    mode: str,
    capability: Capability,
    country: str,
    provider: str | None,
    state: Any,
    failed: str,
) -> None:
    """Refuse (enforce) or report (shadow) a provider Zoiko may not use here. No provider named, no statement, or anything but GRANTED in its
    dates is no."""
    if provider and state is not None and state.entitled:
        return
    reason = failed or (state.reason if state else "no_provider")
    _LOG.warning(
        "provider entitlement check %s: %s %s in %s (%s)",
        "refused" if mode == "enforce" else "would refuse (shadow)",
        provider or "-",
        capability.value,
        country,
        reason,
    )
    if mode == "enforce":
        raise ZidError(
            "PROVIDER_NOT_ENTITLED",
            f"{capability.value} in {country} is not authorized with its provider.",
            http_status=422,
        )


def _release_decision(
    mode: str,
    capability: Capability,
    country: str,
    state: MarketDecision | None,
    failed: str,
) -> None:
    """Refuse (enforce) or report (shadow) a market that has no live release. Open is the only way through."""
    if state is not None and state.open:
        return
    reason = failed or (state.reason if state else "no_release")
    _LOG.warning(
        "market release check %s: %s in %s (%s)",
        "refused" if mode == "enforce" else "would refuse (shadow)",
        capability.value,
        country,
        reason,
    )
    if mode == "enforce":
        raise ZidError(
            "MARKET_NOT_RELEASED",
            f"{capability.value} in {country} has not been released for use.",
            http_status=422,
        )


@asynccontextmanager
async def _read(engine: AsyncEngine, ctx: RequestContext | None) -> AsyncIterator[AsyncConnection]:
    """The regulatory records are platform tables (no tenant data, no row-level security): a request with a tenant reads them in its tenant
    transaction, and one without a tenant yet (a company signing up) in a plain one."""
    if ctx is not None and ctx.tenant_id is not None:
        async with tenant_transaction(engine, ctx) as conn:
            yield conn
    else:
        async with engine.begin() as conn:
            yield conn


# Account registration and discovery are not regulated decisions: they keep the country check only.
UNREGULATED = frozenset({Capability.REGISTRATION, Capability.DISCOVERY})


async def enforce_regulated(
    request: Request, country: str, capability: Capability, ctx: RequestContext | None = None
) -> dict[str, Any] | None:
    """Every regulated entry point's check (EDR-001 plus ZID-RCG-003): the country registry, then the policy pack, the market release, the
    provider entitlement and the kill switch, before anything is started or any provider is asked. Each regulatory check follows its own
    switch (shadow by default); the kill switch is always enforced. Returns the regulatory basis (09.1) when a check applied."""
    enforce(request, country, capability)
    if capability not in UNREGULATED:
        return await enforce_packs(request, ctx, country, capability)
    return None


async def enforce_packs(
    request: Request, ctx: RequestContext | None, country: str, capability: Capability
) -> dict[str, Any] | None:
    """The pack check for a request: the platform switch (`ZID_REGULATORY_PACKS`: off, shadow or enforce), the country registry and the
    packs. Call it after `enforce`, before any provider is asked."""
    mode = str(getattr(request.app.state, "regulatory_packs", "shadow"))
    release_mode = str(getattr(request.app.state, "market_releases", "shadow"))
    entitlement_mode = str(getattr(request.app.state, "provider_entitlements", "shadow"))
    # no early return when every switch is off: the kill switch (CR-078) is checked anyway, and check_packs returns at once when nothing applies
    reg, now, environment = registry(), datetime.now(UTC), environment_of(request)
    if (
        availability(reg, country, capability, now=now, environment=environment)
        is not Availability.ENABLED
    ):
        return None  # every regulatory check is for an ENABLED capability only: nothing to read
    return await check_packs(
        request_engine(request),  # an ENABLED country with no database fails closed here
        ctx,
        mode=mode,
        environment=environment,
        reg=reg,
        country=country,
        capability=capability,
        now=now,
        release_mode=release_mode,
        entitlement_mode=entitlement_mode,
    )


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
    if (
        capability not in UNREGULATED
    ):  # ZID-RCG-003: the same regulatory checks for a tenant's own regulated action
        await enforce_packs(request, None, country, capability)


def tenant_standing_check(
    environment: str,
) -> Callable[[AsyncConnection, str], Awaitable[str | None]]:
    """What an approved change must still find true of the organization's country when it runs (Decision 11, Decision 12 test 07).

    It is the same question `enforce_tenant` asks at the door, answered with a reason code instead of a refusal: a deployed environment
    refuses an organization whose country is unknown, and any environment refuses one whose country has closed the capability since the
    vote. Two valid votes cannot reopen a country the registry closed."""

    async def check(conn: AsyncConnection, tenant_id: str) -> str | None:
        declared = await declared_registration(conn, tenant_id)
        country = NAMESPACE_COUNTRY.get(declared.register_namespace) if declared else None
        if country is None:
            return None if environment in SANDBOX_ENVIRONMENTS else "COUNTRY_NOT_DECLARED"
        try:
            require(
                registry(),
                country,
                Capability.ENTERPRISE_USE,
                now=datetime.now(UTC),
                environment=environment,
            )
        except CountryError as e:
            return e.code
        return None

    return check
