"""The rollout log (`commercial.market_rollout`, migration commercial_0031; ZID-COM-WF-2026-11 WF-I05). Append-only; the current stage is the latest entry."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.domain import rollout as rules
from zid_platform.modules.commercial.domain.catalogue import CommercialError


async def current(conn: AsyncConnection) -> dict[tuple[str, str], dict[str, Any]]:
    """The latest entry for every market that has one, by (family, country)."""
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT DISTINCT ON (family, country) family, country, stage, from_stage, decided_by, decided_at, reason "
                    "FROM commercial.market_rollout ORDER BY family, country, seq DESC"
                )
            )
        )
        .mappings()
        .all()
    )
    return {(r["family"], r["country"]): dict(r) for r in rows}


async def history(conn: AsyncConnection, family: str, country: str) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT seq, stage, from_stage, decided_by, decided_at, reason FROM commercial.market_rollout "
                    "WHERE family = :f AND country = :c ORDER BY seq"
                ),
                {"f": family, "c": country},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def move(
    conn: AsyncConnection, *, family: str, country: str, target: str, decider: str, reason: str
) -> dict[str, Any]:
    """Record one move. The market must be a row of the ACTIVE matrix: a rollout is the controlled step after a signed matrix, never a way round it."""
    in_matrix = (
        await conn.execute(
            text(
                "SELECT 1 FROM commercial.market_matrix_row r JOIN commercial.market_matrix m ON m.matrix_id = r.matrix_id "
                "WHERE m.status = 'ACTIVE' AND r.family = :f AND r.country = :c LIMIT 1"
            ),
            {"f": family, "c": country},
        )
    ).first()
    if in_matrix is None:
        raise CommercialError("ROLLOUT_NOT_IN_ACTIVE_MATRIX", f"{family} in {country}")
    latest = (await current(conn)).get((family, country))
    try:
        rules.check_move(
            latest["stage"] if latest else None,
            latest["from_stage"] if latest else None,
            target,
            decider,
            str(latest["decided_by"]) if latest else None,
        )
    except rules.RolloutError as e:
        raise CommercialError(e.code, e.detail) from e
    await conn.execute(
        text(
            "INSERT INTO commercial.market_rollout (family, country, stage, from_stage, decided_by, reason) "
            "VALUES (:f, :c, :s, :fs, :d, :r)"
        ),
        {
            "f": family,
            "c": country,
            "s": target,
            "fs": latest["stage"] if target == "PAUSED" and latest else None,
            "d": uuid.UUID(decider),
            "r": reason.strip(),
        },
    )
    return {"family": family, "country": country, "stage": target}
