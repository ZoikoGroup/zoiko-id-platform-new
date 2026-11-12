"""Market release lifecycle (ZID-RCG-003 RCG-G10, WF-08; CR-072): the domain rules, the store, the policy packs and the attestations together.

    open_release      a new release (DRAFT, version 1) for a country, product and purpose
    attest_gate       one result for one of the seven evidence gates, by the role that owns it
    freeze_release    pins the approved packs now in force and checks the supplier certifications the author names; computes the hash
    approve_release   one RG-08 approval of the exact hash; the fourth makes the release RELEASEABLE
    launch_release    RELEASEABLE to LAUNCHED; the market's previous live release is withdrawn in the same transaction
    withdraw_release  closes the market at once
    revise_release    a new DRAFT version: nothing carries over
    market_open       is the market open now? fail closed (what the country gate asks)

Who may call these, and the audit record of each, is the API's job (operator_access/api). All work happens inside the caller's transaction.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.application import suspensions
from zid_platform.modules.verification.domain import market_release as md
from zid_platform.modules.verification.domain.attestation import DEFAULT_STALE_AFTER, Scope
from zid_platform.modules.verification.domain.policy_packs import pack_hash
from zid_platform.modules.verification.infrastructure import attestation_store, policy_pack_store
from zid_platform.modules.verification.infrastructure import market_release_store as store


async def _get(conn: AsyncConnection, release_id: str, version: int) -> md.MarketRelease:
    release = await store.load(conn, release_id, version)
    if release is None:
        raise md.ReleaseError("RELEASE_NOT_FOUND", f"{release_id} v{version}")
    return release


async def packs_in_force(conn: AsyncConnection, market: md.MarketKey) -> list[md.PackRef]:
    """The approved policy packs of the market, as a release pins them."""
    packs = await policy_pack_store.load_approved(
        conn, country=market.country, product=market.product, purpose=market.purpose
    )
    return [md.PackRef(p.pack_id, p.version, pack_hash(p)) for p in packs]


async def get_release(conn: AsyncConnection, release_id: str, version: int) -> md.MarketRelease:
    return await _get(conn, release_id, version)


async def list_releases(
    conn: AsyncConnection,
    *,
    country: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[md.MarketRelease]:
    if status is not None and status not in {s.value for s in md.ReleaseStatus}:
        raise md.ReleaseError("RELEASE_STATUS_UNKNOWN", status)
    return await store.list_releases(conn, country=country, status=status, limit=limit)


async def open_release(
    conn: AsyncConnection, release_id: str, market: md.MarketKey, *, author: str, now: datetime
) -> md.MarketRelease:
    if await store.load_latest(conn, release_id) is not None:
        raise md.ReleaseError("RELEASE_EXISTS", f"{release_id} already exists; revise it instead")
    release = md.new_release(release_id, market, author=author)
    await store.insert_draft(conn, release, now=now)
    return release


async def attest_gate(
    conn: AsyncConnection,
    release_id: str,
    version: int,
    *,
    gate: str,
    status: md.GateStatus,
    evidence_ref: str,
    role: str,
    by: str,
    now: datetime,
) -> md.MarketRelease:
    updated = md.record_gate(
        await _get(conn, release_id, version),
        gate=gate,
        status=status,
        evidence_ref=evidence_ref,
        role=role,
        by=by,
        at=now,
    )
    await store.add_gate_result(conn, updated, updated.results[-1])
    return updated


async def freeze_release(
    conn: AsyncConnection,
    release_id: str,
    version: int,
    *,
    suppliers: Sequence[Scope],
    now: datetime,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
) -> md.MarketRelease:
    release = await _get(conn, release_id, version)
    refs = await packs_in_force(conn, release.market)
    decisions = (
        await suspensions.hold_suspended(  # a suspended supplier is never certified (CR-078)
            conn,
            await attestation_store.decide_scopes(
                conn, list(suppliers), at=now, stale_after=stale_after
            ),
            at=now,
        )
    )
    frozen = md.freeze(release, refs, decisions)
    await store.freeze(conn, frozen, now=now)
    return frozen


async def approve_release(
    conn: AsyncConnection,
    release_id: str,
    version: int,
    *,
    role: str,
    approver: str,
    signed_hash: str,
    now: datetime,
) -> md.MarketRelease:
    approved = md.approve_release(
        await _get(conn, release_id, version), role=role, approver=approver, signed_hash=signed_hash
    )
    await store.add_approval(conn, approved, role=role, now=now)
    if approved.status is md.ReleaseStatus.RELEASEABLE:
        await store.set_status(conn, release_id, version, md.ReleaseStatus.RELEASEABLE, now=now)
    return approved


async def launch_release(
    conn: AsyncConnection, release_id: str, version: int, *, now: datetime
) -> md.MarketRelease:
    release = await _get(conn, release_id, version)
    live = md.launch(release)  # refuses anything that is not RELEASEABLE
    for other in await store.load_market(conn, release.market):
        if other.status is md.ReleaseStatus.LAUNCHED and (other.release_id, other.version) != (
            release_id,
            version,
        ):
            await store.set_status(
                conn, other.release_id, other.version, md.ReleaseStatus.WITHDRAWN, now=now
            )
    await store.set_status(conn, release_id, version, md.ReleaseStatus.LAUNCHED, now=now)
    return live


async def withdraw_release(
    conn: AsyncConnection, release_id: str, version: int, *, now: datetime
) -> md.MarketRelease:
    withdrawn = md.withdraw(await _get(conn, release_id, version))
    await store.set_status(conn, release_id, version, md.ReleaseStatus.WITHDRAWN, now=now)
    return withdrawn


async def revise_release(
    conn: AsyncConnection, release_id: str, *, author: str, now: datetime
) -> md.MarketRelease:
    latest = await store.load_latest(conn, release_id)
    if latest is None:
        raise md.ReleaseError("RELEASE_NOT_FOUND", release_id)
    revised = md.revise(latest, author=author)
    await store.insert_draft(conn, revised, now=now)
    return revised


async def market_open(
    conn: AsyncConnection,
    market: md.MarketKey,
    *,
    now: datetime,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
) -> md.MarketDecision:
    """Whether the market is open now: a launched release that still covers the packs in force and whose pinned suppliers are certified on fresh
    evidence. Fail closed on anything else."""
    releases = await store.load_market(conn, market)
    scopes = sorted(
        {s for r in releases if r.status is md.ReleaseStatus.LAUNCHED for s in r.supplier_scopes},
        key=lambda c: (c.entity, c.scheme, c.country, c.service_code),
    )
    suppliers = await suspensions.hold_suspended(  # a kill switch on a pinned supplier closes the market at once (CR-078)
        conn,
        await attestation_store.decide_scopes(conn, scopes, at=now, stale_after=stale_after),
        at=now,
    )
    return md.market_decision(releases, market, await packs_in_force(conn, market), suppliers)
