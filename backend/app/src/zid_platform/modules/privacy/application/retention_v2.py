"""Retention policy reads and evaluations for the /v2 routes (ZID-PRI-004 section 13). Read-only: an evaluation never deletes or changes anything.

An evaluation says what the retention rules would do with one recorded object right now: HELD while any hold covers it (a hold the caller names
that is active, or one that covers the subject or object type), DUE once its date has passed, otherwise RETAIN. It does not dispose of anything:
disposal happens only in the retention sweep, which records its own receipt.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.evidence.ports import hold_status, is_held
from zid_platform.modules.privacy.domain import model as m
from zid_platform.modules.privacy.domain.model import PrivacyError
from zid_platform.modules.privacy.infrastructure import store


def _policy_view(r: Any) -> dict[str, Any]:
    return {
        "policy_id": r.policy_code,
        "version": r.version,
        "object_type": r.object_type,
        "purpose_code": r.purpose_code,
        "jurisdiction_pack_id": r.jurisdiction_pack_id,
        "retention_trigger": r.retention_trigger,
        "retention_days": r.retention_days,
        "action_on_expiry": r.action_on_expiry,
        "backup_erasure_window_days": r.backup_erasure_window_days,
        "legal_hold_suspends": r.legal_hold_suspends,
        "legally_required": r.legally_required,
        "status": r.status,
        "note": "The period is the privacy officer's recorded value, not a legal assertion made by this platform.",
    }


async def policy(conn: AsyncConnection, policy_code: str, version: int | None) -> dict[str, Any]:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM privacy.retention_policy WHERE policy_code = :c "
                "AND (CAST(:v AS integer) IS NULL OR version = :v) ORDER BY version DESC LIMIT 1"
            ),
            {"c": policy_code, "v": version},
        )
    ).first()
    if row is None:
        raise PrivacyError("NOT_FOUND", "unknown retention policy or version")
    return _policy_view(row)


async def evaluate(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    object_ref: str,
    purpose_id: str,
    legal_hold_refs: list[str],
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    entry = (
        await conn.execute(
            text(
                "SELECT * FROM privacy.data_map_entry WHERE object_ref = :o AND purpose_code = :p "
                "AND status <> 'DISPOSED' ORDER BY created_at DESC LIMIT 1"
            ),
            {"o": object_ref, "p": purpose_id},
        )
    ).first()
    if entry is None:
        raise PrivacyError("NOT_FOUND", "no recorded object for this reference and purpose")
    named_active = []
    for ref in legal_hold_refs:
        try:
            uuid.UUID(ref)
        except ValueError as e:
            raise PrivacyError("PRIVACY_SCHEMA_INVALID", "a hold reference is not valid") from e
        if await hold_status(conn, ref) == "ACTIVE":
            named_active.append(ref)
    policy_row = (
        await store.active_retention_policy(conn, entry.retention_policy_code)
        if entry.retention_policy_code
        else None
    )
    suspends = policy_row.legal_hold_suspends if policy_row is not None else True
    covering = bool(
        suspends
        and (
            named_active
            or await is_held(
                conn,
                tenant_id,
                subject_principal_id=str(entry.subject_principal_id),
                object_type=entry.object_type,
                now=now,
            )
        )
    )
    outcome = m.retention_outcome(due=entry.due_at, held=covering, now=now)
    return {
        "object_ref": object_ref,
        "purpose_id": purpose_id,
        "outcome": outcome,
        "due_at": entry.due_at.isoformat() if entry.due_at else None,
        "retention_policy": entry.retention_policy_code,
        "active_holds_named": named_active,
        "unknown_or_inactive_holds_named": [r for r in legal_hold_refs if r not in named_active],
        "evaluated_at": now.isoformat(),
        "note": "An evaluation changes nothing. Disposal happens only in the retention sweep, which keeps its own receipt.",
    }
