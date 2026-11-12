"""Market releases in PostgreSQL (ZID-RCG-003 RCG-G10; CR-072). Platform configuration: not tenant data, no RLS.

The domain rules live in `domain/market_release.py`; the tables repeat the ones that must hold even for a caller that skips the domain code (see
migration verification_0006). A release read back after it was frozen must still hash to the value stored with it: a row edited behind the
application's back is refused, not trusted.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.domain.attestation import Scope as SupplierScope
from zid_platform.modules.verification.domain.market_release import (
    RELEASE_APPROVER_ROLES,
    GateResult,
    GateStatus,
    MarketKey,
    MarketRelease,
    PackRef,
    ReleaseApproval,
    ReleaseError,
    ReleaseStatus,
    compute_hash,
)

_COLUMNS = "release_id, version, country, product, purpose, status, author, pack_refs, supplier_scopes, release_hash"


def _loads(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


async def insert_draft(conn: AsyncConnection, r: MarketRelease, *, now: datetime) -> None:
    if r.status is not ReleaseStatus.DRAFT or r.results or r.approvals:
        raise ReleaseError("RELEASE_NOT_DRAFT", "only an empty draft is inserted")
    await conn.execute(
        text(
            "INSERT INTO verify.market_release (release_id, version, country, product, purpose, status, author, created_at) "
            "VALUES (:id, :v, :c, :pr, :pu, 'DRAFT', :a, :now)"
        ),
        {
            "id": r.release_id,
            "v": r.version,
            "c": r.market.country,
            "pr": r.market.product,
            "pu": r.market.purpose,
            "a": r.author,
            "now": now,
        },
    )


async def add_gate_result(conn: AsyncConnection, r: MarketRelease, result: GateResult) -> None:
    await conn.execute(
        text(
            "INSERT INTO verify.market_release_gate (release_id, version, gate, status, evidence_ref, role, recorded_by, recorded_at) "
            "VALUES (:id, :v, :g, :s, :e, :ro, :by, :at)"
        ),
        {
            "id": r.release_id,
            "v": r.version,
            "g": result.gate,
            "s": result.status.value,
            "e": result.evidence_ref,
            "ro": result.role,
            "by": result.recorded_by,
            "at": result.recorded_at,
        },
    )


async def freeze(conn: AsyncConnection, frozen: MarketRelease, *, now: datetime) -> None:
    """DRAFT to FROZEN with the pinned packs and the hash. The table refuses it unless all seven gates have passed and a pack is pinned."""
    done = await conn.execute(
        text(
            "UPDATE verify.market_release SET status = 'FROZEN', pack_refs = CAST(:refs AS jsonb), supplier_scopes = CAST(:sup AS jsonb), release_hash = :h, frozen_at = :now "
            "WHERE release_id = :id AND version = :v AND status = 'DRAFT'"
        ),
        {
            "refs": json.dumps(
                [
                    {"pack_id": p.pack_id, "version": p.version, "pack_hash": p.pack_hash}
                    for p in frozen.pack_refs
                ]
            ),
            "sup": json.dumps(
                [
                    {
                        "entity": c.entity,
                        "scheme": c.scheme,
                        "country": c.country,
                        "service_code": c.service_code,
                    }
                    for c in frozen.supplier_scopes
                ]
            ),
            "h": frozen.release_hash,
            "now": now,
            "id": frozen.release_id,
            "v": frozen.version,
        },
    )
    if done.rowcount != 1:
        raise ReleaseError("RELEASE_NOT_DRAFT", f"{frozen.release_id} v{frozen.version}")


async def add_approval(
    conn: AsyncConnection, r: MarketRelease, *, role: str, now: datetime
) -> None:
    a = r.approvals.get(role)
    if a is None:
        raise ReleaseError("RELEASE_APPROVAL_MISSING", role)
    await conn.execute(
        text(
            "INSERT INTO verify.market_release_approval (release_id, version, role, approver, release_hash, approved_at) "
            "VALUES (:id, :v, :ro, :ap, :h, :now)"
        ),
        {
            "id": r.release_id,
            "v": r.version,
            "ro": role,
            "ap": a.approver,
            "h": a.release_hash,
            "now": now,
        },
    )


_STAMP = {"RELEASEABLE": "released_at", "LAUNCHED": "launched_at", "WITHDRAWN": "withdrawn_at"}
_FROM = {
    "RELEASEABLE": ("FROZEN",),
    "LAUNCHED": ("RELEASEABLE",),
    "WITHDRAWN": ("RELEASEABLE", "LAUNCHED"),
}


async def set_status(
    conn: AsyncConnection, release_id: str, version: int, status: ReleaseStatus, *, now: datetime
) -> None:
    """FROZEN to RELEASEABLE, RELEASEABLE to LAUNCHED, either to WITHDRAWN. The table refuses RELEASEABLE without four approvals of the hash."""
    if status.value not in _STAMP:
        raise ReleaseError("RELEASE_STATUS_UNKNOWN", status.value)
    done = await conn.execute(
        text(
            f"UPDATE verify.market_release SET status = :s, {_STAMP[status.value]} = :now "
            "WHERE release_id = :id AND version = :v AND status = ANY(CAST(:before AS text[]))"
        ),
        {
            "s": status.value,
            "now": now,
            "id": release_id,
            "v": version,
            "before": list(_FROM[status.value]),
        },
    )
    if done.rowcount != 1:
        raise ReleaseError("RELEASE_NOT_" + status.value, f"{release_id} v{version}")


async def _children(
    conn: AsyncConnection, keys: list[tuple[str, int]]
) -> tuple[
    dict[tuple[str, int], list[GateResult]], dict[tuple[str, int], dict[str, ReleaseApproval]]
]:
    gates: dict[tuple[str, int], list[GateResult]] = {k: [] for k in keys}
    approvals: dict[tuple[str, int], dict[str, ReleaseApproval]] = {k: {} for k in keys}
    if not keys:
        return gates, approvals
    params = {"ids": [k for k, _ in keys], "vs": [v for _, v in keys]}
    where = "(release_id, version) IN (SELECT * FROM unnest(CAST(:ids AS text[]), CAST(:vs AS integer[])))"
    for r in (
        await conn.execute(
            text(
                f"SELECT release_id, version, gate, status, evidence_ref, role, recorded_by, recorded_at FROM verify.market_release_gate WHERE {where} ORDER BY ord"
            ),
            params,
        )
    ).all():
        gates[(r.release_id, r.version)].append(
            GateResult(
                r.gate, GateStatus(r.status), r.evidence_ref, r.role, r.recorded_by, r.recorded_at
            )
        )
    for r in (
        await conn.execute(
            text(
                f"SELECT release_id, version, role, approver, release_hash FROM verify.market_release_approval WHERE {where}"
            ),
            params,
        )
    ).all():
        if r.role in RELEASE_APPROVER_ROLES:
            approvals[(r.release_id, r.version)][r.role] = ReleaseApproval(
                r.approver, r.release_hash
            )
    return gates, approvals


async def _assemble(conn: AsyncConnection, rows: Sequence[Any]) -> list[MarketRelease]:
    gates, approvals = await _children(conn, [(r.release_id, r.version) for r in rows])
    out: list[MarketRelease] = []
    for r in rows:
        k = (r.release_id, r.version)
        release = MarketRelease(
            release_id=r.release_id,
            version=r.version,
            market=MarketKey(r.country, r.product, r.purpose),
            status=ReleaseStatus(r.status),
            author=r.author,
            supplier_scopes=tuple(
                SupplierScope(c["entity"], c["scheme"], c["country"], c["service_code"])
                for c in _loads(r.supplier_scopes)
            ),
            pack_refs=tuple(
                PackRef(p["pack_id"], int(p["version"]), p["pack_hash"])
                for p in _loads(r.pack_refs)
            ),
            results=tuple(gates[k]),
            approvals=approvals[k],
            release_hash=r.release_hash,
        )
        if release.status is not ReleaseStatus.DRAFT and compute_hash(release) != r.release_hash:
            raise ReleaseError(
                "RELEASE_STORED_HASH_MISMATCH", f"{r.release_id} v{r.version}"
            )  # edited behind the application's back
        out.append(release)
    return out


async def load(conn: AsyncConnection, release_id: str, version: int) -> MarketRelease | None:
    rows = (
        await conn.execute(
            text(
                f"SELECT {_COLUMNS} FROM verify.market_release WHERE release_id = :id AND version = :v"
            ),
            {"id": release_id, "v": version},
        )
    ).all()
    return (await _assemble(conn, rows))[0] if rows else None


async def load_latest(conn: AsyncConnection, release_id: str) -> MarketRelease | None:
    rows = (
        await conn.execute(
            text(
                f"SELECT {_COLUMNS} FROM verify.market_release WHERE release_id = :id ORDER BY version DESC LIMIT 1"
            ),
            {"id": release_id},
        )
    ).all()
    return (await _assemble(conn, rows))[0] if rows else None


async def load_market(conn: AsyncConnection, market: MarketKey) -> list[MarketRelease]:
    """Every version of every release of the market, for `market_decision`."""
    rows = (
        await conn.execute(
            text(
                f"SELECT {_COLUMNS} FROM verify.market_release WHERE country = :c AND product = :p AND purpose = :u ORDER BY release_id, version"
            ),
            {"c": market.country.upper(), "p": market.product, "u": market.purpose},
        )
    ).all()
    return await _assemble(conn, rows)


async def list_releases(
    conn: AsyncConnection,
    *,
    country: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[MarketRelease]:
    rows = (
        await conn.execute(
            text(
                f"SELECT {_COLUMNS} FROM verify.market_release "
                "WHERE (CAST(:c AS text) IS NULL OR country = :c) AND (CAST(:s AS text) IS NULL OR status = :s) "
                "ORDER BY created_at DESC, release_id, version DESC LIMIT :n"
            ),
            {"c": country.upper() if country else None, "s": status, "n": max(1, min(limit, 500))},
        )
    ).all()
    return await _assemble(conn, rows)
