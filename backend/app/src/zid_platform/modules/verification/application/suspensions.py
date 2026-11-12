"""Exact-scope suspension service (ZID-RCG-003 RCG-G08; CR-078): apply, lift (the rollback), impact, and the checks the rest of the platform asks.

    apply_suspension     the kill switch: version 1, ACTIVE, at once; returns what it touches
    lift_suspension      the rollback: the next version, LIFTED, by another person
    impact_of            the launched markets, entitlements, certification scopes and claims a scope touches
    blocking_for         the suspensions that stop one call now (the country gate asks)
    hold_suspended       turns a certification decision into HOLD when a suspension covers it (market releases and claims ask)

All of it runs inside the caller's transaction. Who may call it, and the audit record, is the API's job.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.domain import suspension as sp
from zid_platform.modules.verification.domain.attestation import Decision, Outcome
from zid_platform.modules.verification.domain.attestation import Scope as CertScope
from zid_platform.modules.verification.domain.market_release import ReleaseStatus
from zid_platform.modules.verification.infrastructure import (
    attestation_store,
    entitlement_store,
    market_release_store,
    suspension_store,
)


def cert_call(s: CertScope) -> sp.Invocation:
    """A certification scope as the call it stands for: the entity is the provider, the certified service its service."""
    return sp.Invocation(
        provider=s.entity, scheme=s.scheme, country=s.country, service_id=s.service_code
    )


async def blocking_for(
    conn: AsyncConnection, call: sp.Invocation, *, at: datetime
) -> list[sp.Suspension]:
    return sp.blocking(await suspension_store.for_provider(conn, call.provider), call, at)


async def hold_suspended(
    conn: AsyncConnection, decisions: Mapping[CertScope, Decision], *, at: datetime
) -> dict[CertScope, Decision]:
    """A suspended certification is not certified, whatever the register said: the decision becomes HOLD with the suspension named."""
    out: dict[CertScope, Decision] = {}
    for scope, decision in decisions.items():
        stopped = await blocking_for(conn, cert_call(scope), at=at)
        out[scope] = (
            replace(decision, outcome=Outcome.HOLD, reason=f"suspended:{stopped[0].suspension_id}")
            if stopped
            else decision
        )
    return out


async def impact_of(
    conn: AsyncConnection, scope: sp.Scope, *, templates: Sequence[object] = ()
) -> sp.Impact:
    """What the scope touches now: launched releases through their pinned suppliers, entitlements, recorded certification scopes, and claim
    templates through the certifications they rest on. Labels only."""
    launched = await market_release_store.list_releases(
        conn, status=ReleaseStatus.LAUNCHED.value, limit=1000
    )
    markets = [
        (f"{r.release_id}:v{r.version}", cert_call(s)) for r in launched for s in r.supplier_scopes
    ]
    entitlements = [
        (
            f"{e.provider}/{e.country}/{e.capability}/{e.purpose}",
            sp.Invocation(
                provider=e.provider, country=e.country, service_id=e.capability, purpose=e.purpose
            ),
        )
        for e in await entitlement_store.scopes_of(conn, limit=1000)
    ]
    certifications = [
        (f"{c.entity}/{c.scheme}/{c.country}/{c.service_code}", cert_call(c))
        for c in await attestation_store.scopes_of(conn, limit=1000)
    ]
    claims = [
        (f"{t.template_id}:v{t.version}", [cert_call(c) for c in t.certifications])  # type: ignore[attr-defined]
        for t in templates
    ]
    return sp.impact(
        scope,
        markets=markets,
        entitlements=entitlements,
        certifications=certifications,
        claims=claims,
    )


async def apply_suspension(
    conn: AsyncConnection, s: sp.Suspension, *, now: datetime, templates: Sequence[object] = ()
) -> tuple[sp.Suspension, sp.Impact]:
    saved = await suspension_store.apply(conn, s, now=now)
    return saved, await impact_of(conn, saved.scope, templates=templates)


async def lift_suspension(
    conn: AsyncConnection,
    suspension_id: str,
    *,
    by: str,
    reason: str,
    evidence_ref: str,
    now: datetime,
) -> sp.Suspension:
    history = await suspension_store.history(conn, suspension_id)
    if not history:
        raise sp.SuspensionError("SUSPENSION_NOT_FOUND", suspension_id)
    last = history[-1]
    lifted = replace(
        last,
        version=last.version + 1,
        status=sp.Status.LIFTED,
        reason=reason,
        evidence_ref=evidence_ref,
        effective_at=now,
        recorded_by=by,
        recorded_at=now,
    )
    return await suspension_store.lift(conn, lifted, now=now)


async def list_suspensions(
    conn: AsyncConnection, *, at: datetime, active_only: bool
) -> list[list[sp.Suspension]]:
    """Each suspension's versions, oldest first; with `active_only`, only those ACTIVE at `at`."""
    by_id: dict[str, list[sp.Suspension]] = {}
    for s in await suspension_store.all_versions(conn):
        by_id.setdefault(s.suspension_id, []).append(s)
    if active_only:
        active = {
            s.suspension_id for s in sp.in_force([v for vs in by_id.values() for v in vs], at)
        }
        by_id = {k: v for k, v in by_id.items() if k in active}
    return [by_id[k] for k in sorted(by_id)]
