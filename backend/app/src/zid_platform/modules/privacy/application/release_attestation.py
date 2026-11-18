"""The release attestation store (ZID-DTV-WF-04 QA-041; CR-113): the build and capture SDK each approved biometric release was signed for.

Release governance sits with the DPIA release gate, so the record is the privacy module's (`privacy.biometric_release_attestation`, append-only).
The verification module computes the attestation when a second person approves a step up and writes it here, in the approval's transaction; it reads
back the one a live scope rests on. Values only: ids, versions, hashes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_COLUMNS = "scope_id, version, decision_hash, build_ref, sdk_version, attested_by, attested_at, attestation_hash"


async def record_release_attestation(
    conn: AsyncConnection,
    *,
    scope_id: str,
    version: int,
    decision_hash: str,
    build_ref: str,
    sdk_version: str,
    attested_by: str,
    attested_at: datetime,
    attestation_hash: str,
) -> None:
    await conn.execute(
        text(
            f"INSERT INTO privacy.biometric_release_attestation ({_COLUMNS}) VALUES (:s, :v, :d, :b, :k, :by, :at, :h)"
        ),
        {
            "s": scope_id,
            "v": version,
            "d": decision_hash,
            "b": build_ref,
            "k": sdk_version,
            "by": attested_by,
            "at": attested_at,
            "h": attestation_hash,
        },
    )


async def release_attestation_for(
    conn: AsyncConnection, scope_id: str, version: int
) -> dict[str, Any] | None:
    """The attestation the version in force rests on: its own, or that of the approval it narrowed or paused from."""
    r = (
        await conn.execute(
            text(
                f"SELECT {_COLUMNS} FROM privacy.biometric_release_attestation WHERE scope_id = :s AND version <= :v "
                "ORDER BY version DESC LIMIT 1"
            ),
            {"s": scope_id, "v": version},
        )
    ).first()
    return dict(r._mapping) if r is not None else None


__all__ = ["record_release_attestation", "release_attestation_for"]
