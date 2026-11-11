"""Storage for verification cases (ZID-INT-002; `credential_0010`). Inside the caller's tenant transaction (RLS).

A case and its tasks hold references only: the claim check that holds the signed assertion, the identity verification session id. Never a
claimed value or a document.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def insert_case(
    conn: AsyncConnection,
    *,
    case_id: str,
    tenant_id: str,
    subject: str,
    purpose: str,
    country: str,
    subdivision: str | None,
    profile_id: str,
    profile_version: str,
    consent_ref: str | None,
    callback_endpoint_id: str | None,
    client_ref: str | None,
    policy_packs: list[dict[str, Any]],
    created_by: str,
    created_at: datetime,
    expires_at: datetime,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO cred.verification_case (case_id, tenant_id, subject_principal_id, purpose, country, subdivision, "
            "profile_id, profile_version, consent_ref, callback_endpoint_id, client_ref, policy_packs, created_by, "
            "created_at, expires_at) VALUES (:c, :t, :s, :p, :co, :sd, :pi, :pv, :cr, :cb, :cl, CAST(:pk AS jsonb), :by, "
            ":ca, :ex)"
        ),
        {
            "c": uuid.UUID(case_id),
            "t": uuid.UUID(tenant_id),
            "s": uuid.UUID(subject),
            "p": purpose,
            "co": country,
            "sd": subdivision,
            "pi": profile_id,
            "pv": profile_version,
            "cr": consent_ref,
            "cb": uuid.UUID(callback_endpoint_id) if callback_endpoint_id else None,
            "cl": client_ref,
            "pk": json.dumps(policy_packs),
            "by": uuid.UUID(created_by),
            "ca": created_at,
            "ex": expires_at,
        },
    )


async def insert_task(
    conn: AsyncConnection, *, case_id: str, tenant_id: str, check_kind: str
) -> str:
    task_id = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO cred.verification_case_task (task_id, case_id, tenant_id, check_kind) "
            "VALUES (:i, :c, :t, :k)"
        ),
        {
            "i": uuid.UUID(task_id),
            "c": uuid.UUID(case_id),
            "t": uuid.UUID(tenant_id),
            "k": check_kind,
        },
    )
    return task_id


async def load_case(
    conn: AsyncConnection, case_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    row = (
        (
            await conn.execute(
                text(
                    "SELECT * FROM cred.verification_case WHERE case_id = :c"
                    + (" FOR UPDATE" if lock else "")
                ),
                {"c": uuid.UUID(case_id)},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def load_tasks(
    conn: AsyncConnection, case_id: str, *, lock: bool = False
) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT * FROM cred.verification_case_task WHERE case_id = :c ORDER BY check_kind"
                    + (" FOR UPDATE" if lock else "")
                ),
                {"c": uuid.UUID(case_id)},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def complete_task(
    conn: AsyncConnection,
    task_id: str,
    *,
    outcome: str,
    now: datetime,
    credential_check_id: str | None = None,
    identity_verification_id: str | None = None,
) -> None:
    await conn.execute(
        text(
            "UPDATE cred.verification_case_task SET state = 'COMPLETE', outcome = :o, updated_at = :n, "
            "credential_check_id = coalesce(:cc, credential_check_id), "
            "identity_verification_id = coalesce(:iv, identity_verification_id) WHERE task_id = :t"
        ),
        {
            "o": outcome,
            "n": now,
            "cc": uuid.UUID(credential_check_id) if credential_check_id else None,
            "iv": uuid.UUID(identity_verification_id) if identity_verification_id else None,
            "t": uuid.UUID(task_id),
        },
    )


async def link_identity(
    conn: AsyncConnection, task_id: str, verification_id: str, now: datetime
) -> None:
    await conn.execute(
        text(
            "UPDATE cred.verification_case_task SET state = 'IN_PROGRESS', identity_verification_id = :v, "
            "updated_at = :n WHERE task_id = :t"
        ),
        {"v": uuid.UUID(verification_id), "n": now, "t": uuid.UUID(task_id)},
    )


async def close_case(conn: AsyncConnection, case_id: str, state: str, now: datetime) -> None:
    await conn.execute(
        text(
            "UPDATE cred.verification_case SET state = :s, closed_at = :n WHERE case_id = :c AND state = 'PENDING'"
        ),
        {"s": state, "n": now, "c": uuid.UUID(case_id)},
    )
