"""Certification attestations in PostgreSQL (ZID-RCG-003 RCG-G02; CR-073). Platform configuration: not tenant data, no RLS.

Append-only: recording a snapshot never edits another, and an earlier one keeps answering for the moment it was the latest. The domain rules live
in `domain/attestation.py`; the table repeats the ones that must hold even for a caller that skips the domain code (migration verification_0007).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.domain.attestation import (
    DEFAULT_STALE_AFTER,
    Decision,
    Scope,
    Snapshot,
    check_snapshot,
    decide,
)
from zid_platform.modules.verification.domain.certification import CertStatus

_COLUMNS = (
    "snapshot_id, entity, scheme, country, service_code, scheme_version, certificate_ref, component_only, status, valid_from, valid_until, "
    "register_url, register_entry_id, cab, fetched_at, proof_hash, evidence_locator, recorded_by, recorded_at"
)


def _snapshot(r: Any) -> Snapshot:
    return Snapshot(
        snapshot_id=r.snapshot_id,
        scope=Scope(r.entity, r.scheme, r.country, r.service_code),
        scheme_version=r.scheme_version,
        certificate_ref=r.certificate_ref,
        component_only=r.component_only,
        status=CertStatus(r.status),
        valid_from=r.valid_from,
        valid_until=r.valid_until,
        register_url=r.register_url,
        register_entry_id=r.register_entry_id,
        cab=r.cab,
        fetched_at=r.fetched_at,
        proof_hash=r.proof_hash,
        evidence_locator=r.evidence_locator,
        recorded_by=r.recorded_by,
        recorded_at=r.recorded_at,
    )


async def history(conn: AsyncConnection, scope: Scope) -> list[Snapshot]:
    """Every snapshot for the exact scope, oldest first."""
    rows = (
        await conn.execute(
            text(
                f"SELECT {_COLUMNS} FROM verify.certification_attestation "
                "WHERE entity = :e AND scheme = :s AND country = :c AND service_code = :v ORDER BY fetched_at"
            ),
            {
                "e": scope.entity,
                "s": scope.scheme,
                "c": scope.country.upper(),
                "v": scope.service_code,
            },
        )
    ).all()
    return [_snapshot(r) for r in rows]


async def record(conn: AsyncConnection, snapshot: Snapshot, *, now: datetime) -> Snapshot:
    """Check the snapshot against the domain rules and the ones before it, then append it."""
    check_snapshot(snapshot, now=now, previous=await history(conn, snapshot.scope))
    s = snapshot.scope
    await conn.execute(
        text(
            "INSERT INTO verify.certification_attestation (snapshot_id, entity, scheme, country, service_code, scheme_version, certificate_ref, "
            "component_only, status, valid_from, valid_until, register_url, register_entry_id, cab, fetched_at, proof_hash, evidence_locator, "
            "recorded_by, recorded_at) VALUES (:id, :e, :s, :c, :v, :sv, :cr, :co, :st, :vf, :vu, :url, :entry, :cab, :fa, :ph, :ev, :by, :ra)"
        ),
        {
            "id": snapshot.snapshot_id,
            "e": s.entity,
            "s": s.scheme,
            "c": s.country,
            "v": s.service_code,
            "sv": snapshot.scheme_version,
            "cr": snapshot.certificate_ref,
            "co": snapshot.component_only,
            "st": snapshot.status.value,
            "vf": snapshot.valid_from,
            "vu": snapshot.valid_until,
            "url": snapshot.register_url,
            "entry": snapshot.register_entry_id,
            "cab": snapshot.cab,
            "fa": snapshot.fetched_at,
            "ph": snapshot.proof_hash,
            "ev": snapshot.evidence_locator,
            "by": snapshot.recorded_by,
            "ra": snapshot.recorded_at,
        },
    )
    return snapshot


async def decide_scope(
    conn: AsyncConnection,
    scope: Scope,
    *,
    at: datetime,
    stale_after: timedelta | Mapping[str, timedelta] = DEFAULT_STALE_AFTER,
    full_service: bool = True,
) -> Decision:
    return decide(
        await history(conn, scope), scope, at=at, stale_after=stale_after, full_service=full_service
    )


async def decide_scopes(
    conn: AsyncConnection,
    scopes: Sequence[Scope],
    *,
    at: datetime,
    stale_after: timedelta | Mapping[str, timedelta] = DEFAULT_STALE_AFTER,
) -> dict[Scope, Decision]:
    return {s: await decide_scope(conn, s, at=at, stale_after=stale_after) for s in scopes}


async def scopes_of(
    conn: AsyncConnection, *, entity: str | None = None, limit: int = 200
) -> list[Scope]:
    """The distinct scopes anyone has recorded a snapshot for, optionally one entity's."""
    rows = (
        await conn.execute(
            text(
                "SELECT DISTINCT entity, scheme, country, service_code FROM verify.certification_attestation "
                "WHERE CAST(:e AS text) IS NULL OR entity = :e ORDER BY entity, scheme, country, service_code LIMIT :n"
            ),
            {"e": entity, "n": max(1, min(limit, 500))},
        )
    ).all()
    return [Scope(r.entity, r.scheme, r.country, r.service_code) for r in rows]
