"""Release gates for a screening module in a market (ZID-DTV-WF-06 section 22, ENG-20; CR-113): record a signature, open or close a defect, place or release a hold, read the status.

The rules are `domain.release_gates`; this keeps the facts. A scope is GO only when every gate is signed by its named roles, no P0 defect is open and no hold is in place. `is_go` is what the places that
switch a module on ask.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.screening.domain import release_gates as rg
from zid_platform.modules.screening.domain.decision import ScreeningError


def _scope(market: str, module: str) -> rg.Scope:
    try:
        return rg.Scope(market.upper(), rg.Module(module))
    except ValueError as e:
        raise ScreeningError("SCOPE_INVALID", f"{market}/{module}") from e


async def _signoffs(conn: AsyncConnection, scope: rg.Scope) -> list[rg.Signoff]:
    rows = (
        await conn.execute(
            text(
                "SELECT gate, role, actor, evidence_ref, created_at, expires_at, closed_at FROM verify.screening_gate_record "
                "WHERE kind = 'SIGNOFF' AND market = :m AND module = :mo ORDER BY created_at"
            ),
            {"m": scope.market, "mo": scope.module.value},
        )
    ).all()
    return [
        rg.Signoff(
            scope, r.gate, r.role, r.actor, r.evidence_ref, r.created_at, r.expires_at, r.closed_at
        )
        for r in rows
    ]


async def _open(conn: AsyncConnection, scope: rg.Scope, kind: str) -> list[Any]:
    return list(
        (
            await conn.execute(
                text(
                    "SELECT record_id, actor, evidence_ref, created_at FROM verify.screening_gate_record "
                    "WHERE kind = :k AND market = :m AND module = :mo AND closed_at IS NULL ORDER BY created_at"
                ),
                {"k": kind, "m": scope.market, "mo": scope.module.value},
            )
        ).all()
    )


async def status(
    conn: AsyncConnection, market: str, module: str, *, now: datetime
) -> dict[str, Any]:
    scope = _scope(market, module)
    defects = await _open(conn, scope, "DEFECT")
    holds = await _open(conn, scope, "HOLD")
    s = rg.status(
        scope,
        await _signoffs(conn, scope),
        open_p0=len(defects),
        now=now,
        hold=[h.evidence_ref[:60] for h in holds] if holds else (),
    )
    return {
        "market": scope.market,
        "module": scope.module.value,
        "go": s.go,
        "missing": {g: list(m) for g, m in s.gates.items()},
        "blockers": list(s.blockers),
        "open_defects": [
            {
                "record_id": str(d.record_id),
                "ref": d.evidence_ref,
                "opened_at": d.created_at.isoformat(),
            }
            for d in defects
        ],
        "holds": [
            {
                "record_id": str(h.record_id),
                "reason": h.evidence_ref,
                "placed_at": h.created_at.isoformat(),
            }
            for h in holds
        ],
    }


async def is_go(conn: AsyncConnection, market: str, module: str, *, now: datetime) -> bool:
    return bool((await status(conn, market, module, now=now))["go"])


async def sign(
    conn: AsyncConnection,
    market: str,
    module: str,
    *,
    gate: str,
    role: str,
    signer: str,
    evidence_ref: str,
    valid_days: int,
    now: datetime,
) -> dict[str, Any]:
    scope = _scope(market, module)
    new = rg.Signoff(
        scope, gate, role, signer, evidence_ref.strip(), now, now + timedelta(days=valid_days)
    )
    try:
        rg.check_signoff(await _signoffs(conn, scope), new, now=now)
    except rg.GateError as e:
        raise ScreeningError(e.code, e.detail) from e
    record = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO verify.screening_gate_record (record_id, kind, market, module, gate, role, actor, evidence_ref, created_at, expires_at) "
            "VALUES (:r, 'SIGNOFF', :m, :mo, :g, :ro, :a, :e, :at, :x)"
        ),
        {
            "r": record,
            "m": scope.market,
            "mo": scope.module.value,
            "g": gate,
            "ro": role,
            "a": signer,
            "e": new.evidence_ref,
            "at": now,
            "x": new.expires_at,
        },
    )
    return {"record_id": str(record), **await status(conn, market, module, now=now)}


async def _open_record(
    conn: AsyncConnection, kind: str, market: str, module: str, actor: str, ref: str, now: datetime
) -> str:
    scope = _scope(market, module)
    if not ref.strip():
        raise ScreeningError("EVIDENCE_REQUIRED", "say what it is")
    record = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO verify.screening_gate_record (record_id, kind, market, module, actor, evidence_ref, created_at) VALUES (:r, :k, :m, :mo, :a, :e, :at)"
        ),
        {
            "r": record,
            "k": kind,
            "m": scope.market,
            "mo": scope.module.value,
            "a": actor,
            "e": ref.strip(),
            "at": now,
        },
    )
    return str(record)


async def open_defect(
    conn: AsyncConnection, market: str, module: str, *, actor: str, ref: str, now: datetime
) -> dict[str, Any]:
    return {
        "record_id": await _open_record(conn, "DEFECT", market, module, actor, ref, now),
        **await status(conn, market, module, now=now),
    }


async def place_hold(
    conn: AsyncConnection, market: str, module: str, *, actor: str, reason: str, now: datetime
) -> dict[str, Any]:
    """A kill switch: one person, at once. Releasing it is a separate, two-person act."""
    return {
        "record_id": await _open_record(conn, "HOLD", market, module, actor, reason, now),
        **await status(conn, market, module, now=now),
    }


async def signer_of(conn: AsyncConnection, record_id: str) -> str | None:
    """Who gave a signature. None when there is no such sign-off."""
    try:
        key = uuid.UUID(record_id)
    except ValueError:
        return None
    row = (
        await conn.execute(
            text(
                "SELECT actor FROM verify.screening_gate_record WHERE record_id = :r AND kind = 'SIGNOFF'"
            ),
            {"r": key},
        )
    ).first()
    return None if row is None else str(row.actor)


async def close(
    conn: AsyncConnection, record_id: str, *, by: str, kinds: tuple[str, ...], now: datetime
) -> dict[str, Any]:
    """Rescind a sign-off, close a defect or release a hold. Each happens once; a rescinded sign-off is rescinded by its own signer or by a person releasing a hold or closing a defect, never silently."""
    try:
        key = uuid.UUID(record_id)
    except ValueError as e:
        raise ScreeningError("RECORD_NOT_FOUND", record_id) from e
    row = (
        await conn.execute(
            text(
                "SELECT kind, market, module, actor, closed_at FROM verify.screening_gate_record WHERE record_id = :r FOR UPDATE"
            ),
            {"r": key},
        )
    ).first()
    if row is None or row.kind not in kinds:
        raise ScreeningError("RECORD_NOT_FOUND", record_id)
    if row.closed_at is not None:
        raise ScreeningError("RECORD_ALREADY_CLOSED", record_id)
    if row.kind == "HOLD" and row.actor == by:
        raise ScreeningError(
            "HOLD_RELEASED_BY_ITS_OWN_PLACER",
            "a hold is released by someone other than the person who placed it",
        )
    await conn.execute(
        text(
            "UPDATE verify.screening_gate_record SET closed_at = :at, closed_by = :by WHERE record_id = :r"
        ),
        {"at": now, "by": by, "r": key},
    )
    return await status(conn, row.market, row.module, now=now)
