"""Read and record country openings (`verify.country_opening`, CR-127). Append-only: every decision is a new version of one (country, capability); the
newest one is in force."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text

from zid_platform.modules.verification.domain.countries import Opening, OpeningKey

_COLUMNS = "country, capability, version, status, effective_from, approvals, home_cells, reason, recorded_by, recorded_at"


def _opening(r: Any) -> Opening:
    return Opening(
        country=r.country,
        version=int(r.version),
        status=r.status,
        effective_from=r.effective_from,
        approvals=dict(r.approvals or {}),
        home_cells=tuple(r.home_cells or ()),
        reason=r.reason,
        recorded_by=r.recorded_by,
        recorded_at=r.recorded_at,
        capability=r.capability,
    )


async def newest(conn: Any) -> dict[OpeningKey, Opening]:
    """The decision in force for every (country, capability) that has one."""
    rows = (
        await conn.execute(
            text(
                f"SELECT DISTINCT ON (country, capability) {_COLUMNS} "
                "FROM verify.country_opening ORDER BY country, capability, version DESC"
            )
        )
    ).all()
    return {(r.country, r.capability): _opening(r) for r in rows}


async def history(conn: Any, country: str, capability: str) -> list[Opening]:
    rows = (
        await conn.execute(
            text(
                f"SELECT {_COLUMNS} FROM verify.country_opening WHERE country = :c AND capability = :p ORDER BY version DESC"
            ),
            {"c": country, "p": capability},
        )
    ).all()
    return [_opening(r) for r in rows]


async def record(
    conn: Any,
    *,
    country: str,
    capability: str,
    status: str,
    effective_from: datetime | None,
    approvals: dict[str, str],
    home_cells: tuple[str, ...],
    reason: str | None,
    recorded_by: str,
    now: datetime,
) -> Opening:
    """Append the next version for a (country, capability). A lock on the pair keeps two operators from taking the same number."""
    await conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"country_opening:{country}:{capability}"}
    )
    version = (
        await conn.execute(
            text("SELECT coalesce(max(version), 0) + 1 FROM verify.country_opening WHERE country = :c AND capability = :p"),
            {"c": country, "p": capability},
        )
    ).scalar_one()
    await conn.execute(
        text(
            "INSERT INTO verify.country_opening (country, capability, version, status, effective_from, approvals, home_cells, reason, recorded_by, recorded_at) "
            "VALUES (:c, :p, :v, :s, :e, CAST(:a AS jsonb), :h, :r, :b, :t)"
        ),
        {
            "c": country,
            "p": capability,
            "v": int(version),
            "s": status,
            "e": effective_from,
            "a": json.dumps(approvals, sort_keys=True),
            "h": list(home_cells),
            "r": reason,
            "b": recorded_by,
            "t": now,
        },
    )
    return Opening(
        country=country,
        version=int(version),
        status=status,
        effective_from=effective_from,
        approvals=approvals,
        home_cells=home_cells,
        reason=reason,
        recorded_by=recorded_by,
        recorded_at=now,
        capability=capability,
    )


async def refresh(engine: Any, *, force: bool = False) -> None:
    """Load the decisions in force into this process (see country_registry). Cheap and safe to call on every sign-up request: it reads at most every
    few seconds. If the database cannot be read the last known decisions stay (a country that was closed stays closed, one never seen stays shut)."""
    import logging

    from zid_platform.modules.verification.infrastructure import country_registry

    if not (force or country_registry.openings_stale()):
        return
    try:
        async with engine.connect() as conn:
            country_registry.set_openings(await newest(conn))
    except Exception:
        logging.getLogger("zid.country_opening").warning("country openings could not be read", exc_info=True)
