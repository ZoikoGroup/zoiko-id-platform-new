"""WF-05 cases and evidence assertions in PostgreSQL (CR-112; migration verification_0022). Tenant data under row-level security: every call runs in
the caller's tenant transaction."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.domain import check_cases as cc
from zid_platform.modules.verification.domain import check_methods as cm
from zid_platform.modules.verification.domain.assertions import Assertion

_CASE = (
    "case_id, tenant_id, subject_ref, purpose, market, region, requested_methods, available_methods, unavailable_reasons, policy_hash, "
    "status, correlation_id, created_by, created_at, updated_at"
)
_ASSERTION = (
    "assertion_id, tenant_id, case_id, assertion, verdict, method, source, reason_codes, confidence, observed_at, expires_at, recorded_by, "
    "recorded_at"
)


def _case(r: Any) -> cc.Case:
    return cc.Case(
        case_id=str(r.case_id), tenant_id=str(r.tenant_id), subject_ref=r.subject_ref, purpose=r.purpose, market=r.market, region=r.region,
        requested=tuple(cm.CheckMethod(m) for m in r.requested_methods), available=tuple(cm.CheckMethod(m) for m in r.available_methods),
        unavailable_reasons=tuple(r.unavailable_reasons), policy_hash=r.policy_hash, status=cc.CaseStatus(r.status),
        correlation_id=r.correlation_id, created_by=r.created_by, created_at=r.created_at, updated_at=r.updated_at,
    )  # fmt: skip


def _assertion(r: Any) -> cc.AssertionRecord:
    return cc.AssertionRecord(
        assertion_id=str(r.assertion_id), case_id=str(r.case_id), assertion=Assertion(r.assertion), verdict=cc.Verdict(r.verdict),
        method=cm.CheckMethod(r.method), source=r.source, reason_codes=tuple(r.reason_codes),
        confidence=cc.ConfidenceBand(r.confidence) if r.confidence else None, observed_at=r.observed_at, expires_at=r.expires_at,
        recorded_by=r.recorded_by, recorded_at=r.recorded_at,
    )  # fmt: skip


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


async def insert_case(conn: AsyncConnection, c: cc.Case) -> None:
    await conn.execute(
        text(
            f"INSERT INTO verify.check_case ({_CASE}) VALUES (:id, :t, :s, :p, :m, :r, :req, :av, :un, :ph, :st, :cid, :by, :at, :up)"
        ),
        {
            "id": uuid.UUID(c.case_id),
            "t": uuid.UUID(c.tenant_id),
            "s": c.subject_ref,
            "p": c.purpose,
            "m": c.market,
            "r": c.region,
            "req": [m.value for m in c.requested],
            "av": [m.value for m in c.available],
            "un": list(c.unavailable_reasons),
            "ph": c.policy_hash,
            "st": c.status.value,
            "cid": c.correlation_id,
            "by": c.created_by,
            "at": c.created_at,
            "up": c.updated_at,
        },
    )


async def load_case(conn: AsyncConnection, case_id: str, *, lock: bool = False) -> cc.Case | None:
    cid = _uuid(case_id)
    if cid is None:
        return None
    sql = f"SELECT {_CASE} FROM verify.check_case WHERE case_id = :id"
    row = (await conn.execute(text(sql + (" FOR UPDATE" if lock else "")), {"id": cid})).first()
    return _case(row) if row is not None else None


async def update_status(conn: AsyncConnection, c: cc.Case) -> None:
    await conn.execute(
        text("UPDATE verify.check_case SET status = :st, updated_at = :up WHERE case_id = :id"),
        {"st": c.status.value, "up": c.updated_at, "id": uuid.UUID(c.case_id)},
    )


async def insert_assertion(conn: AsyncConnection, tenant_id: str, a: cc.AssertionRecord) -> None:
    await conn.execute(
        text(
            f"INSERT INTO verify.check_assertion ({_ASSERTION}) VALUES (:id, :t, :c, :a, :v, :m, :s, :rc, :cf, :ob, :ex, :by, :at)"
        ),
        {
            "id": uuid.UUID(a.assertion_id),
            "t": uuid.UUID(tenant_id),
            "c": uuid.UUID(a.case_id),
            "a": a.assertion.value,
            "v": a.verdict.value,
            "m": a.method.value,
            "s": a.source,
            "rc": list(a.reason_codes),
            "cf": a.confidence.value if a.confidence else None,
            "ob": a.observed_at,
            "ex": a.expires_at,
            "by": a.recorded_by,
            "at": a.recorded_at,
        },
    )


async def assertions(conn: AsyncConnection, case_id: str) -> list[cc.AssertionRecord]:
    cid = _uuid(case_id)
    if cid is None:
        return []
    rows = (
        await conn.execute(
            text(
                f"SELECT {_ASSERTION} FROM verify.check_assertion WHERE case_id = :c ORDER BY recorded_at, assertion_id"
            ),
            {"c": cid},
        )
    ).all()
    return [_assertion(r) for r in rows]


__all__ = [
    "assertions",
    "insert_assertion",
    "insert_case",
    "load_case",
    "update_status",
]
