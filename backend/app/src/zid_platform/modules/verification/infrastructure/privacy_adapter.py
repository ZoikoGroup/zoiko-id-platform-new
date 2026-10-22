"""The verification module's personal data for the privacy data map (PRIV-001 §6, §31: identity
evidence is D3; assertions keep only commitments to claim values, never the values).

Erasure revokes the subject's assertions (no one can rely on them again) and drops provider
references and pending provider results from sessions. Raw identity evidence never lives here: it
is in the evidence vault under the subject's erasable key, which erasure destroys.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.privacy.ports import register_store

ASSERTIONS = "verify.verification_assertion"
SESSIONS = "verify.verification_session"


async def _locate_assertions(conn: AsyncConnection, subject: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT assertion_id, claim_type, performed_at FROM verify.verification_assertion "
                "WHERE subject_principal_id = :p"
            ),
            {"p": uuid.UUID(subject)},
        )
    ).all()
    return [
        {
            "object_ref": str(r.assertion_id),
            "object_type": f"verification.assertion.{r.claim_type}".lower()[:64],
            "purpose_code": "VERIFY_IDENTITY",
            "data_category": "D3",
            "trigger_at": r.performed_at,
            "region": None,
            "retention_policy_code": "VERIFICATION_RELIANCE",
        }
        for r in rows
    ]


async def _export_assertion(conn: AsyncConnection, entry: Row[Any]) -> dict[str, Any]:
    row = (
        await conn.execute(
            text(
                "SELECT claim_type, verification_method, evidence_types, provider_id, assurance_level, "
                "jurisdiction, status, performed_at, valid_until FROM verify.verification_assertion "
                "WHERE assertion_id = :a"
            ),
            {"a": uuid.UUID(entry.object_ref)},
        )
    ).first()
    return dict(row._mapping) if row else {}


async def _dispose_assertion(conn: AsyncConnection, entry: Row[Any], action: str) -> None:
    await conn.execute(
        text("""
            UPDATE verify.verification_assertion SET provider_reference = NULL,
                status = CASE WHEN status IN ('VALID', 'STALE') THEN 'REVOKED' ELSE status END,
                revoked_at = coalesce(revoked_at, now()),
                reason_codes = array_append(reason_codes, 'ERASED')
            WHERE assertion_id = :a
        """),
        {"a": uuid.UUID(entry.object_ref)},
    )


async def _locate_sessions(conn: AsyncConnection, subject: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT session_id, purpose, created_at FROM verify.verification_session WHERE subject_principal_id = :p"
            ),
            {"p": uuid.UUID(subject)},
        )
    ).all()
    return [
        {
            "object_ref": str(r.session_id),
            "object_type": "verification.session",
            "purpose_code": "VERIFY_IDENTITY",
            "data_category": "D3",
            "trigger_at": r.created_at,
            "region": None,
            "retention_policy_code": "VERIFICATION_SESSION",
        }
        for r in rows
    ]


async def _export_session(conn: AsyncConnection, entry: Row[Any]) -> dict[str, Any]:
    row = (
        await conn.execute(
            text(
                "SELECT purpose, jurisdiction, status, adapter_id, outcome_code, created_at, expires_at "
                "FROM verify.verification_session WHERE session_id = :s"
            ),
            {"s": uuid.UUID(entry.object_ref)},
        )
    ).first()
    return dict(row._mapping) if row else {}


async def _dispose_session(conn: AsyncConnection, entry: Row[Any], action: str) -> None:
    await conn.execute(
        text("""
            UPDATE verify.verification_session SET pending_result = NULL, provider_transaction_ref = NULL,
                updated_at = now(), row_version = row_version + 1
            WHERE session_id = :s
        """),
        {"s": uuid.UUID(entry.object_ref)},
    )


def register() -> None:
    register_store(
        ASSERTIONS,
        locator=_locate_assertions,
        exporter=_export_assertion,
        disposer=_dispose_assertion,
    )
    register_store(
        SESSIONS, locator=_locate_sessions, exporter=_export_session, disposer=_dispose_session
    )


__all__ = ["register"]
