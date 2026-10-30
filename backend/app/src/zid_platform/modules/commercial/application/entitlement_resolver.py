"""The band entitlement resolver in front of the Commercial Estate (ZID-ENG-DEC-008, slice L-8).

One tenant, one plan, one band, and for each of the record's forty features a state a customer can read: Included, Available as an
add-on, Available with upgrade. The decision runs the Estate's own gate first (`catalogue.decide`, over rows read once), then
the band matrix on top of it (`bands.decide_feature`), and applies the commercial enforcement mode: `shadow` and `off` report a
commercial refusal without blocking it, exactly as the Estate does. Compliance results are never shadowed: a region, a missing
provider route or an unknown fact is a deny or a hold in every mode, because a plan is not a way past one.

Reads only. A tenant's plan changes through orders and payments, never here; nothing in this module grants a role or clears a hold.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_errors import ErrorDetail, ZidError

from zid_platform.modules.commercial.application import events
from zid_platform.modules.commercial.application.entitlement_cache import (
    FeatureRow,
    Snapshot,
    SnapshotCache,
    expiry,
    fingerprint,
    from_doc,
    tenant_of,
)
from zid_platform.modules.commercial.domain.bands import (
    BANDS,
    LABEL,
    NOT_IN_REGION,
    PRESERVED,
    BandMatrix,
    EstateView,
    FeatureDecision,
    State,
    decide_feature,
    downgrade_impact,
)
from zid_platform.modules.commercial.domain.catalogue import (
    DEFAULT_PLAN,
    Override,
    PlanCapability,
)
from zid_platform.modules.commercial.domain.catalogue import decide as estate_decide
from zid_platform.modules.commercial.domain.invoicing import NEW_PAID_EXTRAS, restricted
from zid_platform.modules.commercial.infrastructure import catalogue_store as store
from zid_platform.modules.commercial.infrastructure import snapshot_store
from zid_platform.modules.commercial.infrastructure.bands import band_matrix

log = logging.getLogger("zid.commercial")

# Results that are about compliance, not about what was bought: never turned into an allow by a shadow or off mode.
COMPLIANCE = frozenset(
    {
        "NOT_AVAILABLE_IN_REGION",
        "COVERAGE_UNKNOWN",
        "PROVIDER_ROUTE_OUT_OF_COVERAGE",
        "PLAN_NOT_MAPPED_TO_A_BAND",
        "UNKNOWN_FEATURE",
    }
)


@dataclass(frozen=True, slots=True)
class Context:
    """The tenant's commercial facts, read once for a whole decision or view."""

    plan_code: str
    state: str
    source: str
    effective_from: datetime | None
    version: int
    caps: dict[str, PlanCapability]
    overrides: list[Override]
    billing_restricted: bool
    plan_caps: dict[
        str, dict[str, str]
    ]  # every plan -> capability -> mode (for the reconciliation report)


async def load_context(conn: AsyncConnection, now: datetime) -> Context:
    current = await store.active_entitlement(conn)
    plan_code = str(current["plan_code"]) if current else DEFAULT_PLAN
    caps = {c.key: c for c in await store.plan_capabilities(conn, plan_code)}
    billing = (
        await conn.execute(text("SELECT state, safety_hold FROM commercial.billing_account_state"))
    ).first()
    return Context(
        plan_code=plan_code,
        state=str(current["state"]) if current else "ACTIVE",
        source=str(current["source"]) if current else "DEFAULT",
        effective_from=current["effective_from"] if current else None,
        version=int(current["version"]) if current else 0,
        caps=caps,
        overrides=await store.overrides(conn, now),
        billing_restricted=billing is not None
        and NEW_PAID_EXTRAS in restricted(str(billing[0]), bool(billing[1])),
        plan_caps={},
    )


def _estate(ctx: Context, capability: str | None, now: datetime) -> EstateView | None:
    if capability is None:
        return None
    result = estate_decide(
        capability,
        plan_code=ctx.plan_code,
        rule=ctx.caps.get(capability),
        overrides=ctx.overrides,
        used=None,
        quantity=1,
        now=now,
        enforcement="enforce",  # the underlying rule; the commercial mode is applied once, by `effective_outcome`
        state=ctx.state,
        billing_restricted=ctx.billing_restricted,
    )
    return EstateView(result.allowed, result.reason)


def _contract(ctx: Context, capability: str | None, now: datetime) -> bool:
    return capability is not None and any(
        o.capability_key == capability and o.grant_mode and o.live(now) for o in ctx.overrides
    )


SHARED = SnapshotCache()  # one per process; every decision still checks the tenant's fingerprint


@dataclass(frozen=True, slots=True)
class EffectiveDecision:
    decision: FeatureDecision
    enforcement: str
    allowed: bool  # what the caller must obey in this mode
    shadow_denied: bool


def effective_outcome(decision: FeatureDecision, enforcement: str) -> EffectiveDecision:
    if decision.allowed:
        return EffectiveDecision(decision, enforcement, True, False)
    commercial = decision.outcome == "DENY" and not (set(decision.reasons) & COMPLIANCE)
    if commercial and enforcement != "enforce":
        return EffectiveDecision(decision, enforcement, True, enforcement == "shadow")
    return EffectiveDecision(decision, enforcement, False, False)


def decide_one(
    m: BandMatrix,
    ctx: Context,
    feature_id: str,
    now: datetime,
    *,
    enforcement: str,
    region_ok: bool | None = True,
    coverage_ok: bool | None = True,
) -> EffectiveDecision:
    feature = m.features.get(feature_id)
    capability = feature.catalogue if feature else None
    return effective_outcome(
        decide_feature(
            m,
            feature_id,
            ctx.plan_code,
            estate=_estate(ctx, capability, now),
            contract_active=_contract(ctx, capability, now),
            subscription_state=ctx.state,
            region_ok=region_ok,
            coverage_ok=coverage_ok,
        ),
        enforcement,
    )


async def publish(conn: AsyncConnection, snap: Snapshot) -> None:
    """Make this snapshot the tenant's shared one. A change of the commercial state (the fingerprint) raises the revision and is
    announced once; a plain refresh after the TTL is saved quietly. A snapshot already out of date when it gets here is not
    published, so a slow process cannot put an older state back. A failure here never fails the decision: the snapshot
    is still good for this process."""
    try:
        async with conn.begin_nested():
            await snapshot_store.lock(conn, snap.tenant_id)
            if await fingerprint(conn) != snap.fingerprint:
                return
            previous = await snapshot_store.current(conn)
            changed = previous is None or previous[1] != snap.fingerprint
            revision = 1 if previous is None else previous[0] + (1 if changed else 0)
            await snapshot_store.save(
                conn,
                tenant_id=snap.tenant_id,
                revision=revision,
                fingerprint=snap.fingerprint,
                catalog_version=snap.catalog_version,
                issued_at=snap.issued_at,
                expires_at=snap.expires_at,
                doc=snap.doc(),
                signed=snap.signed,
            )
            if changed:
                await events.entitlement_snapshot_published(
                    conn,
                    tenant_id=snap.tenant_id,
                    revision=revision,
                    plan=snap.plan_code,
                    band=snap.band,
                    subscription_state=snap.subscription_state,
                    catalog_version=snap.catalog_version,
                )
    except Exception:
        log.warning("entitlement snapshot could not be published", exc_info=True)


def _row(m: BandMatrix, ctx: Context, feature_id: str, now: datetime) -> FeatureRow:
    d = decide_one(m, ctx, feature_id, now, enforcement="enforce").decision
    return (
        feature_id,
        d.outcome,
        d.reasons,
        d.state.value if d.state else None,
        d.band,
        d.divergent,
        d.upgrade_band,
    )


def _from_row(m: BandMatrix, row: FeatureRow) -> FeatureDecision:
    fid, outcome, reasons, state, band, divergent, upgrade = row
    return FeatureDecision(
        fid, outcome, State(state) if state else None, reasons, band, divergent, upgrade, m.version
    )


def decision_from_snapshot(
    m: BandMatrix,
    snap: Snapshot,
    feature_id: str,
    *,
    region_ok: bool | None,
    coverage_ok: bool | None,
) -> FeatureDecision:
    """The same answer `decide_feature` gives, from a signed snapshot: the baseline first, then the request's own region and
    coverage facts, then what the snapshot holds for the tenant's plan."""
    row = next((r for r in snap.features if r[0] == feature_id), None)
    if row is None:
        return FeatureDecision(
            feature_id, "DENY", None, ("UNKNOWN_FEATURE",), snap.band, False, None, m.version
        )
    if row[3] == State.BASELINE.value or row[2] == ("PLAN_NOT_MAPPED_TO_A_BAND",):
        return _from_row(m, row)
    state = State(row[3]) if row[3] else None
    if region_ok is False:
        return FeatureDecision(
            feature_id,
            "DENY",
            state,
            ("NOT_AVAILABLE_IN_REGION",),
            snap.band,
            False,
            None,
            m.version,
        )
    if region_ok is None or coverage_ok is None:
        return FeatureDecision(
            feature_id, "HOLD", state, ("COVERAGE_UNKNOWN",), snap.band, False, None, m.version
        )
    if coverage_ok is False:
        return FeatureDecision(
            feature_id,
            "HOLD",
            state,
            ("PROVIDER_ROUTE_OUT_OF_COVERAGE",),
            snap.band,
            False,
            None,
            m.version,
        )
    return _from_row(m, row)


async def snapshot_for(
    conn: AsyncConnection, tenant_id: str, m: BandMatrix, now: datetime, cache: SnapshotCache
) -> Snapshot:
    """A usable signed snapshot of the tenant, from the cache when nothing it rests on has changed, else built from fresh reads."""
    fp = await fingerprint(conn)
    usable = cache.usable(tenant_id, fp, now)
    if usable is not None:
        return usable
    shared = await snapshot_store.load(conn)  # built by another process or an earlier run
    if shared is not None:
        _, doc, signed = shared
        theirs = from_doc(doc, signed)
        if cache.adopt(theirs, fp, now):
            return theirs
    ctx = await load_context(conn, now)
    ends = [o.expires_at for o in ctx.overrides if o.expires_at is not None]
    fresh = Snapshot(
        tenant_id=tenant_id,
        plan_code=ctx.plan_code,
        band=m.band_of_plan(ctx.plan_code),
        subscription_state=ctx.state,
        fingerprint=fp,
        catalog_version=m.version,
        issued_at=now,
        expires_at=expiry(now, m.snapshot_ttl_seconds, ends),
        features=tuple(_row(m, ctx, fid, now) for fid in m.features),
        signed="",
    )
    stored = cache.store(fresh)
    await publish(conn, stored)
    return stored


async def decide(
    conn: AsyncConnection,
    feature_id: str,
    *,
    enforcement: str = "shadow",
    region_ok: bool | None = True,
    coverage_ok: bool | None = True,
    now: datetime | None = None,
    cache: SnapshotCache | None = SHARED,
) -> EffectiveDecision:
    """May this tenant (the connection's RLS scope) use `feature_id`? Fails closed on a paid extra only: an unreadable
    entitlement refuses in `enforce` mode and holds in the others, never allows silently."""
    now = now or datetime.now(UTC)
    m = band_matrix()
    try:
        async with conn.begin_nested():
            tenant = await tenant_of(conn) if cache is not None else None
            if cache is not None and tenant is not None:
                snap = await snapshot_for(conn, tenant, m, now, cache)
                return effective_outcome(
                    decision_from_snapshot(
                        m, snap, feature_id, region_ok=region_ok, coverage_ok=coverage_ok
                    ),
                    enforcement,
                )
            ctx = await load_context(conn, now)
    except Exception:  # noqa: BLE001 - an unreadable entitlement must not become a grant
        d = FeatureDecision(
            feature_id, "HOLD", None, ("ENTITLEMENT_UNAVAILABLE",), None, False, None, m.version
        )
        return EffectiveDecision(d, enforcement, enforcement != "enforce", enforcement != "enforce")
    return decide_one(
        m,
        ctx,
        feature_id,
        now,
        enforcement=enforcement,
        region_ok=region_ok,
        coverage_ok=coverage_ok,
    )


TransferGate = Callable[..., Awaitable[Any]]


async def decide_in_region(
    conn: AsyncConnection,
    tenant_id: str,
    feature_id: str,
    *,
    gate: TransferGate,
    destination_cell_id: str,
    data_class: str,
    purpose_code: str,
    enforcement: str = "shadow",
    coverage_ok: bool | None = True,
    now: datetime | None = None,
    cache: SnapshotCache | None = SHARED,
) -> EffectiveDecision:
    """A feature use that would move data to another cell. The tenant's residency gate (SRP and DC rules) answers first and its
    answer is the `region_ok` fact: a plan, however high, never buys a transfer the residency record does not allow. A gate that
    cannot answer is an unknown (a HOLD), not a yes.

    `gate` is the residency module's `authorize_transfer`, handed in by the caller: the tenant module already depends on this one,
    so this one cannot import it back."""
    region_ok: bool | None
    try:
        answer = await gate(
            conn,
            tenant_id,
            data_class=data_class,
            destination_cell_id=destination_cell_id,
            purpose_code=purpose_code,
        )
        region_ok = bool(answer.allowed)
    except Exception:  # noqa: BLE001 - an unreadable residency record is not a permission
        region_ok = None
    return await decide(
        conn,
        feature_id,
        enforcement=enforcement,
        region_ok=region_ok,
        coverage_ok=coverage_ok,
        now=now,
        cache=cache,
    )


async def require_feature(
    conn: AsyncConnection,
    feature_id: str,
    *,
    enforcement: str = "shadow",
    region_ok: bool | None = True,
    coverage_ok: bool | None = True,
) -> EffectiveDecision:
    """The same decision, raising `ENTITLEMENT_REQUIRED` (HTTP 402) for a refusal and 503 for a hold. Only paid-feature creation
    paths call it; the security baseline never does."""
    result = await decide(
        conn, feature_id, enforcement=enforcement, region_ok=region_ok, coverage_ok=coverage_ok
    )
    if not result.allowed:
        d = result.decision
        held = d.outcome == "HOLD"
        raise ZidError(
            "ENTITLEMENT_HOLD" if held else "ENTITLEMENT_REQUIRED",
            "This cannot be confirmed right now." if held else "Your plan does not include this.",
            http_status=503 if held else 402,
            details=[ErrorDetail(field=feature_id, issue=r) for r in d.reasons]
            + ([ErrorDetail(field="upgrade_band", issue=d.upgrade_band)] if d.upgrade_band else []),
        )
    return result


# --------------------------------------------------------------------------------------- views


async def effective(
    conn: AsyncConnection, *, enforcement: str = "shadow", now: datetime | None = None
) -> dict[str, Any]:
    """What this tenant can use, as the customer reads it. Divergences from the Estate are for the owners and are not shown here."""
    now = now or datetime.now(UTC)
    m = band_matrix()
    ctx = await load_context(conn, now)
    band = m.band_of_plan(ctx.plan_code)
    features = []
    for f in m.features.values():
        r = decide_one(m, ctx, f.feature_id, now, enforcement="enforce")
        d = r.decision
        region_blocked = "NOT_AVAILABLE_IN_REGION" in d.reasons
        features.append(
            {
                "feature": f.feature_id,
                "section": f.section,
                "name": f.name,
                "band_state": d.state.value if d.state else None,
                "label": NOT_IN_REGION if region_blocked else (LABEL[d.state] if d.state else None),
                "available": r.allowed,
                "reasons": list(d.reasons),
                "upgrade_band": d.upgrade_band,
                "note": f.note,
            }
        )
    return {
        "plan": ctx.plan_code,
        "band": band,
        "subscription_state": ctx.state,
        "source": ctx.source,
        "effective_from": ctx.effective_from.isoformat() if ctx.effective_from else None,
        "catalog_version": m.version,
        "commercial_enforcement": enforcement,
        "band_matrix_enforcement": m.enforcement,
        "features": features,
        "note": (
            "A plan unlocks commercial capabilities and quotas only. Roles, identity trust, residency, sign-in "
            "freshness and approvals are decided separately, and a higher plan never clears a hold."
        ),
    }


async def downgrade_view(
    conn: AsyncConnection, to_band: str, *, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    m = band_matrix()
    ctx = await load_context(conn, now)
    band = m.band_of_plan(ctx.plan_code)
    if band is None or to_band not in BANDS:
        raise ZidError("BAND_UNKNOWN", "No such band.", http_status=422)
    impact = downgrade_impact(m, band, to_band)
    names = {f.feature_id: f.name for f in m.features.values()}
    return {
        "from_band": impact.from_band,
        "to_band": impact.to_band,
        "becomes_inactive": [{"feature": i, "name": names[i]} for i in impact.becomes_inactive],
        "becomes_add_on": [{"feature": i, "name": names[i]} for i in impact.becomes_add_on],
        "preserved": list(PRESERVED),
        "effective": impact.effective,
        "deletes_anything": impact.deletes_anything,
        "note": "Applied at the authorized renewal date. Integrations, identities, agents and workflows that rely on an inactive "
        "feature stop accepting new work; what they produced is kept.",
    }
