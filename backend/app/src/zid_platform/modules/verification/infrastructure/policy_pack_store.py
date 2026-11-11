"""Policy packs and their approvals in PostgreSQL (ZID-RCG-003 RCG-G01). Platform configuration: not tenant data, no RLS.

The domain rules live in `domain/policy_packs.py`; the tables repeat the ones that must hold even for a caller that skips the
domain code (a published version never changes, approvals are append-only, the author does not approve). A pack read back must
still hash to the value stored with it, so a row edited behind the application's back is refused, not trusted.
"""

from __future__ import annotations

import json
from dataclasses import fields
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.domain.policy_packs import (
    APPROVER_ROLES,
    Approval,
    LegalSource,
    PackError,
    PackKey,
    PackStatus,
    PolicyPack,
    Rules,
    SourceStatus,
    pack_hash,
)

_PACK_COLUMNS = "pack_id, version, country, subdivision, profession, product, purpose, status, sources, rules, author, pack_hash"


def _source_to_json(s: LegalSource) -> dict[str, Any]:
    return {
        "instrument": s.instrument,
        "url": s.url,
        "status": s.status.value,
        "retrieved_at": s.retrieved_at.isoformat(),
        "effective_from": s.effective_from.isoformat(),
        "effective_to": s.effective_to.isoformat() if s.effective_to else None,
        "owner": s.owner,
    }


def _source_from_json(d: dict[str, Any]) -> LegalSource:
    return LegalSource(
        instrument=d["instrument"],
        url=d["url"],
        status=SourceStatus(d["status"]),
        retrieved_at=datetime.fromisoformat(d["retrieved_at"]),
        effective_from=datetime.fromisoformat(d["effective_from"]),
        effective_to=datetime.fromisoformat(d["effective_to"]) if d["effective_to"] else None,
        owner=d["owner"],
    )


def _rules_to_json(r: Rules) -> dict[str, Any]:
    return {
        "evidence_minimum": list(r.evidence_minimum),
        "alternate_routes": list(r.alternate_routes),
        "lawful_basis": r.lawful_basis,
        "expiry_days": r.expiry_days,
        "escalation": r.escalation,
    }


def _rules_from_json(d: dict[str, Any]) -> Rules:
    known = {f.name for f in fields(Rules)}
    raw = {k: v for k, v in d.items() if k in known}
    for tup in ("evidence_minimum", "alternate_routes"):
        raw[tup] = tuple(raw.get(tup, ()))
    return Rules(**raw)


def _loads(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


async def insert_draft(conn: AsyncConnection, pack: PolicyPack, *, now: datetime) -> None:
    """Store a new DRAFT version. Approvals are added one by one with `add_approval`."""
    if pack.status is not PackStatus.DRAFT:
        raise PackError("PACK_NOT_DRAFT", "only a draft is inserted; publish moves it on")
    await conn.execute(
        text(
            "INSERT INTO verify.policy_pack (pack_id, version, country, subdivision, profession, product, purpose, status, "
            "sources, rules, author, pack_hash, created_at) VALUES (:pack_id, :version, :country, :subdivision, :profession, "
            ":product, :purpose, 'DRAFT', CAST(:sources AS jsonb), CAST(:rules AS jsonb), :author, :hash, :now)"
        ),
        {
            "pack_id": pack.pack_id,
            "version": pack.version,
            "country": pack.key.country,
            "subdivision": pack.key.subdivision,
            "profession": pack.key.profession,
            "product": pack.key.product,
            "purpose": pack.key.purpose,
            "sources": json.dumps([_source_to_json(s) for s in pack.sources]),
            "rules": json.dumps(_rules_to_json(pack.rules)),
            "author": pack.author,
            "hash": pack_hash(pack),
            "now": now,
        },
    )


async def add_approval(
    conn: AsyncConnection, pack: PolicyPack, *, role: str, now: datetime
) -> None:
    """Record one approval already made in the domain (`approve`). The table refuses the author, a repeat approver and a stale hash."""
    a = pack.approvals.get(role)
    if a is None:
        raise PackError("PACK_APPROVAL_MISSING", role)
    await conn.execute(
        text(
            "INSERT INTO verify.policy_pack_approval (pack_id, version, role, approver, pack_hash, approved_at) "
            "VALUES (:pack_id, :version, :role, :approver, :hash, :now)"
        ),
        {
            "pack_id": pack.pack_id,
            "version": pack.version,
            "role": role,
            "approver": a.approver,
            "hash": a.pack_hash,
            "now": now,
        },
    )


async def mark_published(
    conn: AsyncConnection, pack_id: str, version: int, *, now: datetime
) -> None:
    """DRAFT to APPROVED. The table refuses it unless the four approvals of the stored hash exist."""
    done = await conn.execute(
        text(
            "UPDATE verify.policy_pack SET status = 'APPROVED', published_at = :now "
            "WHERE pack_id = :p AND version = :v AND status = 'DRAFT'"
        ),
        {"p": pack_id, "v": version, "now": now},
    )
    if done.rowcount != 1:
        raise PackError("PACK_NOT_DRAFT", f"{pack_id} v{version}")


async def mark_retired(conn: AsyncConnection, pack_id: str, version: int, *, now: datetime) -> None:
    done = await conn.execute(
        text(
            "UPDATE verify.policy_pack SET status = 'RETIRED', retired_at = :now "
            "WHERE pack_id = :p AND version = :v AND status = 'APPROVED'"
        ),
        {"p": pack_id, "v": version, "now": now},
    )
    if done.rowcount != 1:
        raise PackError("PACK_NOT_APPROVED", f"{pack_id} v{version}")


async def _approvals(
    conn: AsyncConnection, pairs: list[tuple[str, int]]
) -> dict[tuple[str, int], dict[str, Approval]]:
    out: dict[tuple[str, int], dict[str, Approval]] = {p: {} for p in pairs}
    if not pairs:
        return out
    rows = (
        await conn.execute(
            text(
                "SELECT pack_id, version, role, approver, pack_hash FROM verify.policy_pack_approval "
                "WHERE (pack_id, version) IN (SELECT * FROM unnest(CAST(:ids AS text[]), CAST(:vs AS integer[])))"
            ),
            {"ids": [p for p, _ in pairs], "vs": [v for _, v in pairs]},
        )
    ).all()
    for r in rows:
        if r.role in APPROVER_ROLES:
            out[(r.pack_id, r.version)][r.role] = Approval(r.approver, r.pack_hash)
    return out


def _pack(row: Any, approvals: dict[str, Approval]) -> PolicyPack:
    pack = PolicyPack(
        pack_id=row.pack_id,
        key=PackKey(
            country=row.country,
            product=row.product,
            purpose=row.purpose,
            subdivision=row.subdivision,
            profession=row.profession,
        ),
        version=row.version,
        status=PackStatus(row.status),
        sources=tuple(_source_from_json(s) for s in _loads(row.sources)),
        rules=_rules_from_json(_loads(row.rules)),
        author=row.author,
        approvals=approvals,
    )
    if (
        pack_hash(pack) != row.pack_hash
    ):  # a row edited behind the application's back is refused, not trusted
        raise PackError("PACK_STORED_HASH_MISMATCH", f"{row.pack_id} v{row.version}")
    return pack


async def load(conn: AsyncConnection, pack_id: str, version: int) -> PolicyPack | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_PACK_COLUMNS} FROM verify.policy_pack WHERE pack_id = :p AND version = :v"
            ),
            {"p": pack_id, "v": version},
        )
    ).first()
    if row is None:
        return None
    return _pack(row, (await _approvals(conn, [(pack_id, version)]))[(pack_id, version)])


async def load_approved(
    conn: AsyncConnection, *, country: str, product: str, purpose: str
) -> list[PolicyPack]:
    """Every APPROVED version for the market, ready for `resolve`; the resolver picks among them and fails closed."""
    rows = (
        await conn.execute(
            text(
                f"SELECT {_PACK_COLUMNS} FROM verify.policy_pack "
                "WHERE status = 'APPROVED' AND country = :c AND product = :p AND purpose = :u ORDER BY pack_id, version"
            ),
            {"c": country.upper(), "p": product, "u": purpose},
        )
    ).all()
    approvals = await _approvals(conn, [(r.pack_id, r.version) for r in rows])
    return [_pack(r, approvals[(r.pack_id, r.version)]) for r in rows]
