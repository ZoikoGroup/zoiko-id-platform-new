"""Recovery codes, recovery cases and RC-4 lockbox policies in PostgreSQL (ZID-AUTH-001 §4, §14).

Inside the caller's tenant transaction (RLS). Codes and case handles are stored only as SHA-256;
a code is spent with a conditional UPDATE, so it works exactly once.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.authentication.domain.recovery import (
    LockboxPolicy,
    RecoveryError,
    normalize_code,
)


def sha256(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


async def replace_codes(
    conn: AsyncConnection, *, tenant_id: str, principal_id: str, codes: list[str], now: datetime
) -> None:
    await conn.execute(
        text(
            "UPDATE authn.recovery_code SET revoked_at = :now WHERE principal_id = :p "
            "AND used_at IS NULL AND revoked_at IS NULL"
        ),
        {"p": uuid.UUID(principal_id), "now": now},
    )
    batch = uuid.uuid4()
    for code in codes:
        await conn.execute(
            text(
                "INSERT INTO authn.recovery_code (code_hash, tenant_id, principal_id, batch_id, "
                "created_at) VALUES (:h, :t, :p, :b, :now)"
            ),
            {
                "h": sha256(normalize_code(code)),
                "t": uuid.UUID(tenant_id),
                "p": uuid.UUID(principal_id),
                "b": batch,
                "now": now,
            },
        )


async def spend_code(conn: AsyncConnection, code: str, now: datetime) -> str | None:
    row = (
        await conn.execute(
            text(
                "UPDATE authn.recovery_code SET used_at = :now WHERE code_hash = :h "
                "AND used_at IS NULL AND revoked_at IS NULL RETURNING principal_id"
            ),
            {"h": sha256(normalize_code(code)), "now": now},
        )
    ).first()
    return str(row.principal_id) if row else None


@dataclass(frozen=True, slots=True)
class Case:
    recovery_id: str
    tenant_id: str
    principal_id: str
    recovery_class: str
    method: str
    status: str
    opened_by: str
    reproof_assertion_id: str | None
    approval_request_id: str | None
    cooling_off_until: datetime | None
    created_at: datetime
    expires_at: datetime


_COLS = (
    "recovery_id, tenant_id, principal_id, recovery_class, method, status, opened_by, "
    "reproof_assertion_id, approval_request_id, cooling_off_until, created_at, expires_at"
)


def _case(r: object) -> Case:
    m = r._mapping  # type: ignore[attr-defined]
    return Case(
        recovery_id=str(m["recovery_id"]),
        tenant_id=str(m["tenant_id"]),
        principal_id=str(m["principal_id"]),
        recovery_class=m["recovery_class"],
        method=m["method"],
        status=m["status"],
        opened_by=m["opened_by"],
        reproof_assertion_id=str(m["reproof_assertion_id"]) if m["reproof_assertion_id"] else None,
        approval_request_id=str(m["approval_request_id"]) if m["approval_request_id"] else None,
        cooling_off_until=m["cooling_off_until"],
        created_at=m["created_at"],
        expires_at=m["expires_at"],
    )


async def open_case(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    principal_id: str,
    recovery_class: str,
    method: str,
    status: str,
    opened_by: str,
    handle: str,
    now: datetime,
    expires_at: datetime,
) -> Case:
    rid = str(uuid.uuid4())
    await conn.execute(
        text(
            f"INSERT INTO authn.recovery_case ({_COLS}, handle_hash) VALUES (:id, :t, :p, :cls, :m, "
            ":st, :by, NULL, NULL, NULL, :now, :exp, :h)"
        ),
        {
            "id": uuid.UUID(rid),
            "t": uuid.UUID(tenant_id),
            "p": uuid.UUID(principal_id),
            "cls": recovery_class,
            "m": method,
            "st": status,
            "by": opened_by,
            "now": now,
            "exp": expires_at,
            "h": sha256(handle),
        },
    )
    return await load(conn, rid, lock=False)


async def load(conn: AsyncConnection, recovery_id: str, *, lock: bool = True) -> Case:
    try:
        rid = uuid.UUID(recovery_id)
    except ValueError as e:
        raise RecoveryError("RECOVERY_NOT_FOUND") from e
    row = (
        await conn.execute(
            text(
                f"SELECT {_COLS} FROM authn.recovery_case WHERE recovery_id = :r"
                + (" FOR UPDATE" if lock else "")
            ),
            {"r": rid},
        )
    ).first()
    if row is None:
        raise RecoveryError("RECOVERY_NOT_FOUND")
    return _case(row)


async def by_handle(conn: AsyncConnection, recovery_id: str, handle: str) -> Case:
    case = await load(conn, recovery_id)
    row = (
        await conn.execute(
            text("SELECT 1 FROM authn.recovery_case WHERE recovery_id = :r AND handle_hash = :h"),
            {"r": uuid.UUID(recovery_id), "h": sha256(handle)},
        )
    ).first()
    if row is None:
        raise RecoveryError("RECOVERY_NOT_FOUND")  # same answer as a wrong id: no probing
    return case


async def update(
    conn: AsyncConnection,
    case: Case,
    *,
    status: str,
    now: datetime,
    reproof_assertion_id: str | None = None,
    approval_request_id: str | None = None,
    cooling_off_until: datetime | None = None,
    denied_reason: str | None = None,
) -> None:
    await conn.execute(
        text(
            "UPDATE authn.recovery_case SET status = :st, "
            "reproof_assertion_id = coalesce(:rp, reproof_assertion_id), "
            "approval_request_id = coalesce(:ap, approval_request_id), "
            "cooling_off_until = coalesce(cooling_off_until, :co), "
            "denied_reason = coalesce(:dr, denied_reason), "
            "completed_at = CASE WHEN :st IN ('COMPLETED', 'DENIED', 'EXPIRED') THEN :now "
            "ELSE completed_at END WHERE recovery_id = :id"
        ),
        {
            "id": uuid.UUID(case.recovery_id),
            "st": status,
            "rp": uuid.UUID(reproof_assertion_id) if reproof_assertion_id else None,
            "ap": uuid.UUID(approval_request_id) if approval_request_id else None,
            "co": cooling_off_until,
            "dr": denied_reason,
            "now": now,
        },
    )


async def load_policy(conn: AsyncConnection, principal_id: str) -> LockboxPolicy | None:
    row = (
        await conn.execute(
            text(
                "SELECT principal_id, approvers, k, cooling_off_hours, revision "
                "FROM authn.recovery_policy WHERE principal_id = :p"
            ),
            {"p": uuid.UUID(principal_id)},
        )
    ).first()
    if row is None:
        return None
    return LockboxPolicy(
        str(row.principal_id),
        tuple(str(a) for a in row.approvers),
        row.k,
        timedelta(hours=row.cooling_off_hours),
        row.revision,
    )


async def put_policy(
    conn: AsyncConnection, tenant_id: str, policy: LockboxPolicy, by: str, now: datetime
) -> int:
    row = (
        await conn.execute(
            text(
                "INSERT INTO authn.recovery_policy (tenant_id, principal_id, approvers, k, "
                "cooling_off_hours, updated_by, updated_at) VALUES (:t, :p, :a, :k, :h, :by, :now) "
                "ON CONFLICT (principal_id) DO UPDATE SET approvers = EXCLUDED.approvers, "
                "k = EXCLUDED.k, cooling_off_hours = EXCLUDED.cooling_off_hours, "
                "revision = authn.recovery_policy.revision + 1, updated_by = EXCLUDED.updated_by, "
                "updated_at = EXCLUDED.updated_at RETURNING revision"
            ),
            {
                "t": uuid.UUID(tenant_id),
                "p": uuid.UUID(policy.principal_id),
                "a": [uuid.UUID(a) for a in policy.approvers],
                "k": policy.k,
                "h": int(policy.cooling_off.total_seconds() // 3600),
                "by": uuid.UUID(by),
                "now": now,
            },
        )
    ).one()
    return int(row.revision)


async def delete_policy(conn: AsyncConnection, principal_id: str) -> bool:
    r = await conn.execute(
        text("DELETE FROM authn.recovery_policy WHERE principal_id = :p"),
        {"p": uuid.UUID(principal_id)},
    )
    return r.rowcount == 1


async def open_cases_for(conn: AsyncConnection, principal_id: str) -> list[Case]:
    rows = (
        await conn.execute(
            text(
                f"SELECT {_COLS} FROM authn.recovery_case WHERE principal_id = :p AND status NOT IN "
                "('COMPLETED', 'DENIED', 'EXPIRED')"
            ),
            {"p": uuid.UUID(principal_id)},
        )
    ).all()
    return [_case(r) for r in rows]


async def code_status(conn: AsyncConnection, principal_id: str) -> tuple[int, datetime | None]:
    """(unused, unrevoked codes, when the current batch was issued)."""
    row = (
        await conn.execute(
            text(
                "SELECT count(*) FILTER (WHERE used_at IS NULL) AS left, max(created_at) AS issued "
                "FROM authn.recovery_code WHERE principal_id = :p AND revoked_at IS NULL"
            ),
            {"p": uuid.UUID(principal_id)},
        )
    ).one()
    return int(row.left or 0), row.issued
