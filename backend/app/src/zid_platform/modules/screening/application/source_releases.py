"""Governed release of an official sanctions source (ZID-DTV-WF-06 S10, ENG-03 to ENG-05; CR-113).

`stage` parses and validates a fetched corpus and stores it inactive: screening cannot read it. `approve` records one person's approval, with the anomalies they acknowledged by name.
`publish` makes a release the single active corpus once two people other than the stager approved it. `rollback` reactivates an earlier release. Every state change is made under one lock
per source, so two racing publications cannot leave two active corpora. The corpus in use is never changed by a corpus that fails validation.

The existing `ingest_list` (a direct load) is unchanged; a deployment that wants no unreviewed import uses only this path.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.screening.domain import monitoring_watch as ms
from zid_platform.modules.screening.domain import source_release as sr
from zid_platform.modules.screening.domain.decision import ScreeningError
from zid_platform.modules.screening.domain.lists import Entry, ListId
from zid_platform.modules.screening.infrastructure import release_store as store
from zid_platform.modules.screening.infrastructure.parsers import PARSERS, ListFormatError

OFFICIAL = frozenset(
    {
        ListId.OFAC_SDN,
        ListId.OFAC_NON_SDN,
        ListId.UK_SANCTIONS,
        ListId.EU_CONSOLIDATED,
        ListId.UN_SC,
    }
)


def _parse(list_id: ListId, files: Mapping[str, bytes]) -> tuple[list[Entry], bool]:
    """(entries, schema valid). A file that does not parse is not an error here: it is staged as quarantined, so the attempt and its receipt are on record."""
    parser = PARSERS[list_id]
    try:
        if list_id in (ListId.OFAC_SDN, ListId.OFAC_NON_SDN):
            entries = parser(files["sdn"], files.get("alt"), files.get("add"))
        else:
            entries = parser(files["file"])
    except (KeyError, ListFormatError):
        return [], False
    return entries, True


def _digest(files: Mapping[str, bytes]) -> str:
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode())
        h.update(files[name])
    return h.hexdigest()


def view(release: sr.Release) -> dict[str, Any]:
    """What an operator sees: the receipt, the validation, the approvals. No entry, no name."""
    r = release.receipt
    return {
        "release_id": release.release_id,
        "source_id": release.source_id,
        "state": release.state.value,
        "staged_by": release.staged_by,
        "receipt": {
            "url": r.url,
            "sha256": r.sha256,
            "fetched_at": r.fetched_at.isoformat(),
            "published_at": r.published_at.isoformat() if r.published_at else None,
            "item_count": r.item_count,
            "schema_valid": r.schema_valid,
        },
        "blocked": list(release.validation.blocked),
        "flags": list(release.validation.flags),
        "approvals": [
            {
                "approver": a.approver,
                "at": a.at.isoformat(),
                "acknowledged": sorted(a.acknowledged),
                "kind": a.kind,
            }
            for a in release.approvals
        ],
        "activated_at": release.activated_at.isoformat() if release.activated_at else None,
        "superseded_at": release.superseded_at.isoformat() if release.superseded_at else None,
        "publishable": sr.publishable(release) if release.state is sr.State.VALIDATED else [],
    }


async def stage(
    conn: AsyncConnection,
    list_id: ListId,
    files: Mapping[str, bytes],
    *,
    url: str,
    staged_by: str,
    now: datetime,
    published_at: datetime | None = None,
    advertised_sha256: str | None = None,
) -> dict[str, Any]:
    """Record a fetched corpus. It is VALIDATED (waiting for two approvers) or QUARANTINED (it can never be released); either way it is inactive."""
    if list_id not in OFFICIAL:
        raise ScreeningError("SOURCE_NOT_REGISTERED", list_id.value)
    entries, schema_valid = _parse(list_id, files)
    sha = _digest(files)
    receipt = sr.Receipt(
        source_id=list_id.value,
        url=url,
        sha256=sha,
        fetched_at=now,
        published_at=published_at,
        item_count=len(entries),
        schema_valid=schema_valid,
        advertised_sha256=advertised_sha256,
    )
    active = await store.active_version(conn, list_id)
    validation = sr.validate(
        receipt,
        active_count=None if active is None else int(active.entry_count),
        active_sha256=None if active is None else str(active.content_hash),
    )
    release_id = str(uuid.uuid4())
    release = sr.stage(release_id, receipt, staged_by=staged_by, validation=validation)
    version_id = None
    if release.state is sr.State.VALIDATED:
        existing = await store.version_for(conn, list_id, sha)
        version_id = (
            existing[0]
            if existing
            else await store.insert_version(
                conn,
                list_id,
                version=sha[:12],
                content_hash=sha,
                entries=entries,
                source_ref=url[:500],
                fetched_at=now,
                now=now,
            )
        )
    await store.insert_release(conn, release, version_id=version_id, staged_at=now)
    return view(release)


async def approve(
    conn: AsyncConnection,
    release_id: str,
    *,
    approver: str,
    acknowledged: Sequence[str],
    now: datetime,
) -> dict[str, Any]:
    got = await store.get_release(conn, release_id, lock=True)
    if got is None:
        raise ScreeningError("RELEASE_NOT_FOUND", release_id)
    release, _ = got
    try:
        updated = sr.approve(release, approver=approver, acknowledged=acknowledged, at=now)
    except sr.ReleaseError as e:
        raise ScreeningError(e.code, e.detail) from e
    await store.save_state(conn, updated)
    return view(updated)


_FOLLOW_ON: dict[
    str, dict[str, int]
] = {}  # what a release switch did to cases and watches, for the response (per process, read once)


async def _follow_on(
    conn: AsyncConnection,
    list_id: str,
    old_version: uuid.UUID | None,
    new_version: uuid.UUID,
    release_id: str,
    now: datetime,
) -> dict[str, int]:
    """What changing the corpus means for what already relied on it: every case that relied on an entry that was delisted or changed is held again, and every active watch on the source is asked to
    screen again. A release that changes nothing does neither."""
    from zid_platform.modules.screening.application import screening_requests, watches

    old = await store.entry_digests(conn, old_version) if old_version is not None else {}
    delta = ms.diff(old, await store.entry_digests(conn, new_version))
    if delta.empty:
        return {"cases_reopened": 0, "watches_asked": 0}
    reopened = await screening_requests.reopen_for_delta(
        conn, list_id, delta.removed | delta.changed, release_id=release_id, now=now
    )
    asked = await watches.request_rescreen_for_source(conn, list_id, now=now)
    return {"cases_reopened": reopened, "watches_asked": asked}


async def _apply(
    conn: AsyncConnection,
    before: Sequence[sr.Release],
    after: Sequence[sr.Release],
    versions: Mapping[str, uuid.UUID | None],
    *,
    list_id: str,
    now: datetime,
) -> sr.Release:
    old = {r.release_id: r for r in before}
    target: sr.Release | None = None
    changed = [r for r in after if r != old[r.release_id]]
    # the one that becomes active is saved last: the index allows one active release per source, so the one it replaces is superseded first
    for r in sorted(changed, key=lambda x: x.state is sr.State.ACTIVE):
        await store.save_state(conn, r)
        if r.state is sr.State.ACTIVE and old[r.release_id].state is not sr.State.ACTIVE:
            target = r
    if target is None:
        raise ScreeningError("RELEASE_NOT_PUBLISHABLE", "nothing became active")
    version_id = versions[target.release_id]
    if version_id is None:
        raise ScreeningError("RELEASE_NOT_PUBLISHABLE", "a quarantined release has no corpus")
    previous = await store.active_version(conn, ListId(list_id))
    await store.make_active(conn, list_id, version_id, at=now)
    _FOLLOW_ON[target.release_id] = await _follow_on(
        conn,
        list_id,
        None if previous is None else previous.version_id,
        version_id,
        target.release_id,
        now,
    )
    return target


async def publish(conn: AsyncConnection, release_id: str, *, now: datetime) -> dict[str, Any]:
    got = await store.get_release(conn, release_id)
    if got is None:
        raise ScreeningError("RELEASE_NOT_FOUND", release_id)
    list_id = got[0].source_id
    await store.lock_list(conn, list_id)
    rows = await store.releases_of(conn, list_id)
    history = [r for r, _ in rows]
    versions = {r.release_id: v for r, v in rows}
    try:
        after = sr.publish(history, release_id, at=now)
    except sr.ReleaseError as e:
        raise ScreeningError(e.code, e.detail) from e
    target = await _apply(conn, history, after, versions, list_id=list_id, now=now)
    return {**view(target), **_FOLLOW_ON.pop(target.release_id, {})}


async def approve_rollback(
    conn: AsyncConnection, release_id: str, *, approver: str, now: datetime
) -> dict[str, Any]:
    """One person's approval to reactivate an earlier release, stored on that release. Two such approvals, neither from the stager, let `rollback` run."""
    got = await store.get_release(conn, release_id, lock=True)
    if got is None:
        raise ScreeningError("RELEASE_NOT_FOUND", release_id)
    try:
        updated = sr.approve_rollback(got[0], approver=approver, at=now)
    except sr.ReleaseError as e:
        raise ScreeningError(e.code, e.detail) from e
    await store.save_state(conn, updated)
    return view(updated)


async def rollback(conn: AsyncConnection, to_release_id: str, *, now: datetime) -> dict[str, Any]:
    got = await store.get_release(conn, to_release_id)
    if got is None:
        raise ScreeningError("RELEASE_NOT_FOUND", to_release_id)
    approvers = sr.rollback_approvers(got[0])
    list_id = got[0].source_id
    await store.lock_list(conn, list_id)
    rows = await store.releases_of(conn, list_id)
    history = [r for r, _ in rows]
    versions = {r.release_id: v for r, v in rows}
    try:
        after = sr.rollback(history, to_release_id, approvers=approvers, at=now)
    except sr.ReleaseError as e:
        raise ScreeningError(e.code, e.detail) from e
    target = await _apply(conn, history, after, versions, list_id=list_id, now=now)
    return {**view(target), **_FOLLOW_ON.pop(target.release_id, {})}


async def history(conn: AsyncConnection, list_id: ListId) -> list[dict[str, Any]]:
    return [view(r) for r, _ in await store.releases_of(conn, list_id.value, newest_first=True)]


async def get(conn: AsyncConnection, release_id: str) -> dict[str, Any]:
    got = await store.get_release(conn, release_id)
    if got is None:
        raise ScreeningError("RELEASE_NOT_FOUND", release_id)
    return view(got[0])


async def compare(conn: AsyncConnection, release_id: str) -> dict[str, Any]:
    """What a release would change against the corpus in use: counts, and the source references (never names) of what was added, removed and changed."""
    got = await store.get_release(conn, release_id)
    if got is None:
        raise ScreeningError("RELEASE_NOT_FOUND", release_id)
    release, version_id = got
    if version_id is None:
        raise ScreeningError(
            "RELEASE_HAS_NO_CORPUS", "a quarantined release has no corpus to compare"
        )
    active = await store.active_version(conn, ListId(release.source_id))
    old = await store.entry_digests(conn, active.version_id) if active is not None else {}
    new = await store.entry_digests(conn, version_id)
    delta = ms.diff(old, new)
    return {
        "release_id": release_id,
        "against_active": active is not None,
        "added": len(delta.added),
        "removed": len(delta.removed),
        "changed": len(delta.changed),
        "removed_refs": sorted(delta.removed)[:200],
        "changed_refs": sorted(delta.changed)[:200],
    }


async def health(
    conn: AsyncConnection, list_id: ListId, *, now: datetime, max_age: timedelta
) -> dict[str, Any]:
    rows = await store.releases_of(conn, list_id.value)
    h = sr.health([r for r, _ in rows], list_id.value, now=now, max_age=max_age)
    return {
        "source_id": h.source_id,
        "freshness": h.freshness.value,
        "last_validated_at": h.last_validated_at.isoformat() if h.last_validated_at else None,
        "snapshot": h.snapshot,
    }
