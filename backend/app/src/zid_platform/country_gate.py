"""Country gate for HTTP entry points (ZID-EDR-001 §3: country permissions at every API, job, webhook and action).

One place turns the registry's answer into a refusal, so every route that starts a regulated capability asks the
same question the same way: is this capability open for this country, here, now? Unknown or unapproved is no.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
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
    gate_decision,
    market_releases,
    pack_needed,
    policy_packs,
    registry,
    release_model,
    require,
)

MarketDecision = release_model.MarketDecision
MarketKey = release_model.MarketKey
ReleaseError = release_model.ReleaseError

_LOG = logging.getLogger("zid.regulatory")

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


async def check_packs(
    engine: AsyncEngine,
    ctx: RequestContext,
    *,
    mode: str,
    environment: str,
    reg: Any,
    country: str,
    capability: Capability,
    now: datetime,
    release_mode: str = "off",
) -> None:
    """ZID-RCG-003 T01/T02: besides being ENABLED in the country registry, a regulated capability needs an approved policy pack for the
    market, or it is refused before any provider is called (enforce) or only reported (shadow). The registry stays the baseline: a capability
    that is not ENABLED there is decided by `enforce`, and no pack is read for it. The product is the capability, the purpose the standard one.
    A pack that no longer matches its stored hash counts as no pack. Nothing here ever turns into a pass.

    `release_mode` adds RCG-G10: the market also needs a LAUNCHED market release that still covers the packs in force and whose pinned supplier
    certifications are certified on fresh evidence (`market_releases.market_open`). It is read in the same transaction and decided after the
    pack, with the same off, shadow and enforce meaning. A release that cannot be read counts as not released."""
    if release_mode not in GATE_MODES:
        raise ValueError(f"unknown market release mode {release_mode!r}")
    state = availability(reg, country, capability, now=now, environment=environment)
    need_pack = pack_needed(mode, state)
    need_release = release_mode != "off" and state is Availability.ENABLED
    if not need_pack and not need_release:
        return
    resolution = None
    failed = ""
    market_state: MarketDecision | None = None
    release_failed = ""
    try:
        async with tenant_transaction(engine, ctx) as conn:
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
    except PolicyPackError as e:  # the transaction itself failed on a pack
        failed = str(e)
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


async def enforce_packs(
    request: Request, ctx: RequestContext, country: str, capability: Capability
) -> None:
    """The pack check for a request: the platform switch (`ZID_REGULATORY_PACKS`: off, shadow or enforce), the country registry and the
    packs. Call it after `enforce`, before any provider is asked."""
    mode = str(getattr(request.app.state, "regulatory_packs", "shadow"))
    release_mode = str(getattr(request.app.state, "market_releases", "shadow"))
    if mode == "off" and release_mode == "off":
        return
    await check_packs(
        request_engine(request),
        ctx,
        mode=mode,
        environment=environment_of(request),
        reg=registry(),
        country=country,
        capability=capability,
        now=datetime.now(UTC),
        release_mode=release_mode,
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
