"""Storage for governed source releases (CR-113). Fixed statements only. A release is never deleted, and what it recorded at staging never changes: the table says so too."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.screening.domain import source_release as sr
from zid_platform.modules.screening.domain.lists import Entry, ListId

_COLUMNS = (
    "release_id, list_id, version_id, state, staged_by, receipt, validation, approvals, "
    "staged_at, activated_at, superseded_at"
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dt(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def receipt_to_json(r: sr.Receipt) -> str:
    return json.dumps(
        {
            "source_id": r.source_id,
            "url": r.url,
            "sha256": r.sha256,
            "fetched_at": _iso(r.fetched_at),
            "published_at": _iso(r.published_at),
            "item_count": r.item_count,
            "schema_valid": r.schema_valid,
            "advertised_sha256": r.advertised_sha256,
        }
    )


def receipt_from_json(d: dict[str, Any]) -> sr.Receipt:
    return sr.Receipt(
        source_id=d["source_id"],
        url=d["url"],
        sha256=d["sha256"],
        fetched_at=datetime.fromisoformat(d["fetched_at"]),
        published_at=_dt(d.get("published_at")),
        item_count=int(d["item_count"]),
        schema_valid=bool(d["schema_valid"]),
        advertised_sha256=d.get("advertised_sha256"),
    )


def _row(r: Any) -> tuple[sr.Release, uuid.UUID | None]:
    receipt = receipt_from_json(r.receipt)
    validation = sr.Validation(tuple(r.validation["blocked"]), tuple(r.validation["flags"]))
    approvals = tuple(
        sr.Approval(
            a["approver"],
            datetime.fromisoformat(a["at"]),
            frozenset(a.get("acknowledged", ())),
            a.get("kind", "PUBLISH"),
        )
        for a in r.approvals
    )
    release = sr.Release(
        str(r.release_id),
        r.list_id,
        receipt,
        r.staged_by,
        validation,
        sr.State(r.state),
        approvals,
        r.activated_at,
        r.superseded_at,
    )
    return release, r.version_id


async def version_for(
    conn: AsyncConnection, list_id: ListId, content_hash: str
) -> tuple[uuid.UUID, bool] | None:
    row = (
        await conn.execute(
            text(
                "SELECT version_id, active FROM verify.sanction_list_version "
                "WHERE list_id = :l AND content_hash = :h"
            ),
            {"l": list_id.value, "h": content_hash},
        )
    ).first()
    return None if row is None else (row.version_id, bool(row.active))


async def active_version(conn: AsyncConnection, list_id: ListId) -> Any | None:
    return (
        await conn.execute(
            text(
                "SELECT version_id, content_hash, entry_count FROM verify.sanction_list_version "
                "WHERE list_id = :l AND active"
            ),
            {"l": list_id.value},
        )
    ).first()


async def insert_version(
    conn: AsyncConnection,
    list_id: ListId,
    *,
    version: str,
    content_hash: str,
    entries: list[Entry],
    source_ref: str,
    fetched_at: datetime,
    now: datetime,
) -> uuid.UUID:
    """A corpus that is loaded but not active: screening cannot read it until a release makes it the active version."""
    from zid_platform.modules.screening.infrastructure import store

    version_id = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO verify.sanction_list_version (version_id, list_id, version, content_hash, entry_count, "
            "source_ref, fetched_at, active, created_at) VALUES (:v, :l, :n, :h, :c, :s, :f, false, :now)"
        ),
        {
            "v": version_id,
            "l": list_id.value,
            "n": version,
            "h": content_hash,
            "c": len(entries),
            "s": source_ref,
            "f": fetched_at,
            "now": now,
        },
    )
    await store.insert_entries(conn, version_id, entries)
    return version_id


async def insert_release(
    conn: AsyncConnection, release: sr.Release, *, version_id: uuid.UUID | None, staged_at: datetime
) -> None:
    await conn.execute(
        text(
            "INSERT INTO verify.sanction_list_release (release_id, list_id, version_id, state, staged_by, receipt, "
            "validation, approvals, staged_at) VALUES (:r, :l, :v, :s, :by, CAST(:rc AS jsonb), CAST(:va AS jsonb), "
            "'[]'::jsonb, :at)"
        ),
        {
            "r": uuid.UUID(release.release_id),
            "l": release.source_id,
            "v": version_id,
            "s": release.state.value,
            "by": release.staged_by,
            "rc": receipt_to_json(release.receipt),
            "va": json.dumps(
                {
                    "blocked": list(release.validation.blocked),
                    "flags": list(release.validation.flags),
                }
            ),
            "at": staged_at,
        },
    )


async def get_release(
    conn: AsyncConnection, release_id: str, *, lock: bool = False
) -> tuple[sr.Release, uuid.UUID | None] | None:
    sql = (
        f"SELECT {_COLUMNS} FROM verify.sanction_list_release WHERE release_id = :r FOR UPDATE"
        if lock
        else f"SELECT {_COLUMNS} FROM verify.sanction_list_release WHERE release_id = :r"
    )
    try:
        key = uuid.UUID(release_id)
    except ValueError:
        return None
    row = (await conn.execute(text(sql), {"r": key})).first()
    return None if row is None else _row(row)


async def releases_of(
    conn: AsyncConnection, list_id: str, *, newest_first: bool = False
) -> list[tuple[sr.Release, uuid.UUID | None]]:
    sql = (
        f"SELECT {_COLUMNS} FROM verify.sanction_list_release WHERE list_id = :l ORDER BY staged_at DESC, release_id"
        if newest_first
        else f"SELECT {_COLUMNS} FROM verify.sanction_list_release WHERE list_id = :l ORDER BY staged_at, release_id"
    )
    return [_row(r) for r in (await conn.execute(text(sql), {"l": list_id})).all()]


async def lock_list(conn: AsyncConnection, list_id: str) -> None:
    """One publication of one source at a time, so two racing publishes cannot leave two active corpora."""
    await conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
        {"k": f"sanction-list-release:{list_id}"},
    )


async def save_state(conn: AsyncConnection, release: sr.Release) -> None:
    await conn.execute(
        text(
            "UPDATE verify.sanction_list_release SET state = :s, approvals = CAST(:a AS jsonb), "
            "activated_at = :act, superseded_at = :sup WHERE release_id = :r"
        ),
        {
            "s": release.state.value,
            "a": json.dumps(
                [
                    {
                        "approver": a.approver,
                        "at": a.at.isoformat(),
                        "acknowledged": sorted(a.acknowledged),
                        "kind": a.kind,
                    }
                    for a in release.approvals
                ]
            ),
            "act": release.activated_at,
            "sup": release.superseded_at,
            "r": uuid.UUID(release.release_id),
        },
    )


async def make_active(
    conn: AsyncConnection, list_id: str, version_id: uuid.UUID, *, at: datetime
) -> None:
    """Switch the corpus screening reads. The old one is switched off first (one active version per list), the new one's fetch time is the time it was released."""
    await conn.execute(
        text(
            "UPDATE verify.sanction_list_version SET active = false WHERE list_id = :l AND active"
        ),
        {"l": list_id},
    )
    await conn.execute(
        text(
            "UPDATE verify.sanction_list_version SET active = true, fetched_at = :f WHERE version_id = :v"
        ),
        {"f": at, "v": version_id},
    )


async def entry_digests(conn: AsyncConnection, version_id: uuid.UUID) -> dict[str, str]:
    rows = (
        await conn.execute(
            text(
                "SELECT source_ref, encode(sha256(convert_to(primary_name || data::text, 'UTF8')), 'hex') AS digest "
                "FROM verify.sanction_list_entry WHERE version_id = :v"
            ),
            {"v": version_id},
        )
    ).all()
    return {r.source_ref: r.digest for r in rows}
