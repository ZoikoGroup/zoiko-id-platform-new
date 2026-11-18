"""Persistence of governed trust packages (`evidence.trust_package`, evidence_0010; CR-107). Row-level security scopes every query to the caller's tenant."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

KIND = "CSCA"
_LIGHT = (
    "package_id, tenant_id, kind, seq, version, state, source_uri, source_note, package_sha256, anchor_sha256, list_status, list_reasons, "
    "list_signer, certificate_count, manifest_digest, staged_by, staged_at, validated_at, decided_by, decided_at, decision_reason, "
    "activated_by, activated_at, ended_at, ended_by, ended_reason"
)


class StaleVersion(RuntimeError):
    """Another request changed the package first."""


def _row(r: Any) -> dict[str, Any]:
    return dict(r._mapping)


async def insert(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    package_id: str,
    source_uri: str,
    source_note: str | None,
    package_sha256: str,
    package_der: bytes,
    anchor_pems: list[str],
    anchor_sha256: list[str],
    staged_by: str,
    now: datetime,
) -> int:
    await conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
        {"k": f"trust_package:{tenant_id}:{KIND}"},
    )
    seq = (
        await conn.execute(
            text("SELECT coalesce(max(seq), 0) + 1 FROM evidence.trust_package WHERE kind = :k"),
            {"k": KIND},
        )
    ).scalar_one()
    await conn.execute(
        text(
            "INSERT INTO evidence.trust_package (package_id, tenant_id, kind, seq, source_uri, source_note, package_sha256, package_der, "
            "anchor_pems, anchor_sha256, staged_by, staged_at) VALUES (:id, :t, :k, :seq, :uri, :note, :sha, :der, :pems, :asha, :by, :at)"
        ),
        {
            "id": uuid.UUID(package_id),
            "t": uuid.UUID(tenant_id),
            "k": KIND,
            "seq": seq,
            "uri": source_uri,
            "note": source_note,
            "sha": package_sha256,
            "der": package_der,
            "pems": anchor_pems,
            "asha": anchor_sha256,
            "by": uuid.UUID(staged_by),
            "at": now,
        },
    )
    return int(seq)


async def load(
    conn: AsyncConnection, package_id: str, *, lock: bool = False, heavy: bool = False
) -> dict[str, Any] | None:
    try:
        pid = uuid.UUID(package_id)
    except ValueError:
        return None
    cols = "*" if heavy else _LIGHT
    row = (
        await conn.execute(
            text(
                f"SELECT {cols} FROM evidence.trust_package WHERE package_id = :i"
                + (" FOR UPDATE" if lock else "")
            ),
            {"i": pid},
        )
    ).first()
    return _row(row) if row else None


async def listing(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(f"SELECT {_LIGHT} FROM evidence.trust_package WHERE kind = :k ORDER BY seq DESC"),
        {"k": KIND},
    )
    return [_row(r) for r in rows]


async def active(conn: AsyncConnection) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text("SELECT * FROM evidence.trust_package WHERE kind = :k AND state = 'ACTIVE'"),
            {"k": KIND},
        )
    ).first()
    return _row(row) if row else None


async def states(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(
            "SELECT package_id, seq, state, staged_by, activated_at FROM evidence.trust_package WHERE kind = :k"
        ),
        {"k": KIND},
    )
    return [_row(r) for r in rows]


_UPDATE = text(
    """
    UPDATE evidence.trust_package SET
        state = CASE WHEN :f_state THEN :state ELSE state END,
        list_status = CASE WHEN :f_list_status THEN :list_status ELSE list_status END,
        list_reasons = CASE WHEN :f_list_reasons THEN :list_reasons ELSE list_reasons END,
        list_signer = CASE WHEN :f_list_signer THEN :list_signer ELSE list_signer END,
        certificates = CASE WHEN :f_certificates THEN cast(:certificates AS jsonb) ELSE certificates END,
        certificate_count = CASE WHEN :f_certificate_count THEN :certificate_count ELSE certificate_count END,
        manifest_digest = CASE WHEN :f_manifest_digest THEN :manifest_digest ELSE manifest_digest END,
        validated_at = CASE WHEN :f_validated_at THEN :validated_at ELSE validated_at END,
        decided_by = CASE WHEN :f_decided_by THEN :decided_by ELSE decided_by END,
        decided_at = CASE WHEN :f_decided_at THEN :decided_at ELSE decided_at END,
        decision_reason = CASE WHEN :f_decision_reason THEN :decision_reason ELSE decision_reason END,
        activated_by = CASE WHEN :f_activated_by THEN :activated_by ELSE activated_by END,
        activated_at = CASE WHEN :f_activated_at THEN :activated_at ELSE activated_at END,
        ended_at = CASE WHEN :f_ended_at THEN :ended_at ELSE ended_at END,
        ended_by = CASE WHEN :f_ended_by THEN :ended_by ELSE ended_by END,
        ended_reason = CASE WHEN :f_ended_reason THEN :ended_reason ELSE ended_reason END,
        version = version + 1
    WHERE package_id = :i AND version = :v
    RETURNING version
    """
)
_SETTABLE = (
    "state", "list_status", "list_reasons", "list_signer", "certificates", "certificate_count", "manifest_digest", "validated_at",
    "decided_by", "decided_at", "decision_reason", "activated_by", "activated_at", "ended_at", "ended_by", "ended_reason",
)  # fmt: skip


async def update(
    conn: AsyncConnection, package_id: str, *, expected_version: int, **fields: Any
) -> int:
    """Change the named columns of one package (a column not named is left alone, a column named None is cleared), bumping the version.
    One fixed statement: no SQL is assembled from names. Returns the new version."""
    bad = set(fields) - set(_SETTABLE)
    if bad:
        raise ValueError(f"not a settable column: {sorted(bad)}")
    params: dict[str, Any] = {"i": uuid.UUID(package_id), "v": expected_version}
    for key in _SETTABLE:
        value = fields.get(key)
        params[f"f_{key}"] = key in fields
        if key == "certificates":
            value = json.dumps(value if value is not None else [])
        elif key.endswith("_by") and value is not None:
            value = uuid.UUID(str(value))
        elif key == "list_reasons" and value is None:
            value = []
        params[key] = value
    row = (await conn.execute(_UPDATE, params)).first()
    if row is None:
        raise StaleVersion(package_id)
    return int(row[0])
