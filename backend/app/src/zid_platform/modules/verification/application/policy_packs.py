"""Policy pack lifecycle (ZID-RCG-003 RCG-G01, WF-02): the domain rules and the store together, inside the caller's transaction.

    open_draft     a new pack, version 1 (or a new market), as a DRAFT
    revise_pack    a change to a published pack: a new DRAFT version, never an edit; one open draft per pack
    sign           one approval of the exact pack hash, by someone who is not the author and holds no other role on it
    publish_pack   DRAFT to APPROVED, only with a source in force and the four approvals of the current hash
    retire_pack    APPROVED to RETIRED; rolling back is retiring the newer version, so the last approved one governs again
    governing_pack which approved pack governs a request, failing closed (UNAVAILABLE / REVIEW, never a default pass)

Who may call these (a catalogued action, step-up) is the API's job and is not built here. No audit event is emitted yet: an event
must be declared in the event catalogue first. A pack whose stored hash no longer matches raises `PACK_STORED_HASH_MISMATCH`
instead of being skipped, so tampering is loud and never turns into a pass.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.domain import policy_packs as domain
from zid_platform.modules.verification.domain.policy_packs import (
    LegalSource,
    PackError,
    PackStatus,
    PolicyPack,
    Resolution,
    Rules,
)
from zid_platform.modules.verification.infrastructure import policy_pack_store as store


async def _get(conn: AsyncConnection, pack_id: str, version: int) -> PolicyPack:
    pack = await store.load(conn, pack_id, version)
    if pack is None:
        raise PackError("PACK_NOT_FOUND", f"{pack_id} v{version}")
    return pack


async def open_draft(conn: AsyncConnection, pack: PolicyPack, *, now: datetime) -> PolicyPack:
    if pack.status is not PackStatus.DRAFT or pack.approvals:
        raise PackError("PACK_NOT_DRAFT", "a new pack starts as a draft with no approvals")
    if await store.load_latest(conn, pack.pack_id) is not None:
        raise PackError("PACK_EXISTS", f"{pack.pack_id} already exists; revise it instead")
    await store.insert_draft(conn, pack, now=now)
    return pack


async def revise_pack(
    conn: AsyncConnection,
    pack_id: str,
    *,
    author: str,
    now: datetime,
    sources: tuple[LegalSource, ...] | None = None,
    rules: Rules | None = None,
) -> PolicyPack:
    latest = await store.load_latest(conn, pack_id)
    if latest is None:
        raise PackError("PACK_NOT_FOUND", pack_id)
    if latest.status is PackStatus.DRAFT:
        raise PackError("PACK_DRAFT_OPEN", f"{pack_id} v{latest.version} is still a draft")
    if sources is None and rules is None:
        raise PackError("PACK_NO_CHANGE", "a revision changes the sources or the rules")
    new = domain.revise(latest, author=author, sources=sources, rules=rules)
    if new.sources == latest.sources and new.rules == latest.rules:
        raise PackError("PACK_NO_CHANGE", "the revision is identical to the latest version")
    await store.insert_draft(conn, new, now=now)
    return new


async def sign(
    conn: AsyncConnection,
    pack_id: str,
    version: int,
    *,
    role: str,
    approver: str,
    signed_hash: str,
    now: datetime,
) -> PolicyPack:
    """`signed_hash` is the hash the approver was shown; an approval of any other version is refused."""
    signed = domain.approve(
        await _get(conn, pack_id, version), role=role, approver=approver, signed_hash=signed_hash
    )
    await store.add_approval(conn, signed, role=role, now=now)
    return signed


async def publish_pack(
    conn: AsyncConnection, pack_id: str, version: int, *, now: datetime
) -> PolicyPack:
    published = domain.publish(await _get(conn, pack_id, version), at=now)
    await store.mark_published(conn, pack_id, version, now=now)
    return published


async def retire_pack(
    conn: AsyncConnection, pack_id: str, version: int, *, now: datetime
) -> PolicyPack:
    pack = await _get(conn, pack_id, version)
    if pack.status is not PackStatus.APPROVED:
        raise PackError("PACK_NOT_APPROVED", f"{pack_id} v{version} is {pack.status.value}")
    await store.mark_retired(conn, pack_id, version, now=now)
    return domain.retire(pack)


async def governing_pack(
    conn: AsyncConnection,
    *,
    country: str,
    product: str,
    purpose: str,
    at: datetime,
    subdivision: str | None = None,
    profession: str | None = None,
) -> Resolution:
    packs = await store.load_approved(conn, country=country, product=product, purpose=purpose)
    return domain.resolve(
        packs,
        country=country,
        product=product,
        purpose=purpose,
        at=at,
        subdivision=subdivision,
        profession=profession,
    )
