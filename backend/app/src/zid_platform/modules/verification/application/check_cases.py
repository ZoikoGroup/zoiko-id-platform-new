"""WF-05 verification cases: open, requirements, evidence, record an assertion, move, close (ZID-DTV-WF-05 §06, §19, A01, A09; CR-112).

    open_case        bind a new case to the tenant, subject reference, purpose, market and region, under the live eligibility answer
    requirements     what the case may still use, re-read live: a method paused after the case opened is shown unavailable now
    record_assertion one piece of evidence, from a method the case was cleared for (called by the method adapters, later WF-05 steps)
    evidence         each assertion on its own, privacy-filtered, never an overall result
    close            the tenant ends the case; it takes no new evidence

Everything runs in the caller's tenant transaction, so row-level security keeps one tenant's cases from another.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.application import check_methods, check_policies
from zid_platform.modules.verification.domain import check_cases as cc
from zid_platform.modules.verification.domain import check_methods as cm
from zid_platform.modules.verification.domain import check_policy as cp
from zid_platform.modules.verification.domain import privacy_overlay as po
from zid_platform.modules.verification.domain.assertions import Assertion
from zid_platform.modules.verification.infrastructure import check_case_store as store

UNAVAILABLE_MESSAGE = "This step is unavailable."


async def open_case(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    subject_ref: str,
    purpose: str,
    market: str,
    region: str,
    requested: Sequence[cm.CheckMethod],
    overlays: Mapping[str, po.Overlay],
    correlation_id: str,
    created_by: str,
    now: datetime,
    adapters: Mapping[cm.CheckMethod, cm.Adapter] | None = None,
    retention: Mapping[str, cm.RetentionRule] | None = None,
) -> tuple[cc.Case, check_methods.Eligibility]:
    market = market.upper()
    requested = cp.requested_with_policy(
        list(requested), await check_policies.approved(conn, market, purpose)
    )
    answer = await check_methods.eligibility(
        conn, tenant_id=tenant_id, country=market, purpose=purpose, overlays=overlays, now=now,
        rollout_key=subject_ref, adapters=adapters, retention=retention,
    )  # fmt: skip
    case = cc.open_case(
        case_id=str(uuid.uuid4()), tenant_id=tenant_id, subject_ref=subject_ref, purpose=purpose, market=market, region=region,
        requested=requested, eligibility=answer.methods, policy_hash=answer.policy_hash, correlation_id=correlation_id,
        created_by=created_by, now=now,
    )  # fmt: skip
    await store.insert_case(conn, case)
    return case, answer


async def _case(conn: AsyncConnection, case_id: str, *, lock: bool = False) -> cc.Case:
    case = await store.load_case(conn, case_id, lock=lock)
    if case is None:
        raise cc.CaseError("CASE_NOT_FOUND", case_id)
    return case


async def get_case(conn: AsyncConnection, case_id: str) -> cc.Case:
    return await _case(conn, case_id)


async def recent(conn: AsyncConnection) -> list[cc.Case]:
    return await store.recent_cases(conn)


def case_view(case: cc.Case) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "status": case.status.value,
        "subject_ref": case.subject_ref,
        "purpose": case.purpose,
        "market": case.market,
        "region": case.region,
        "requested_methods": [m.value for m in case.requested],
        "available_methods": [m.value for m in case.available],
        "unavailable_reasons": list(case.unavailable_reasons),
        "policy_hash": case.policy_hash,
        "message": None if case.available else UNAVAILABLE_MESSAGE,
        "created_at": case.created_at.isoformat(),
    }


async def requirements(
    conn: AsyncConnection,
    case_id: str,
    *,
    overlays: Mapping[str, po.Overlay],
    now: datetime,
    adapters: Mapping[cm.CheckMethod, cm.Adapter] | None = None,
    retention: Mapping[str, cm.RetentionRule] | None = None,
) -> dict[str, Any]:
    """What the case asked for, and for each method whether it may be used NOW (a later pause or a lost retention approval shows at once). A method
    the case was not cleared for at opening stays unusable for this case even if it has since become available."""
    case = await _case(conn, case_id)
    live = await check_methods.eligibility(
        conn, tenant_id=case.tenant_id, country=case.market, purpose=case.purpose, overlays=overlays, now=now,
        rollout_key=case.subject_ref, adapters=adapters, retention=retention,
    )  # fmt: skip
    by_method = {m.method: m for m in live.methods}
    methods = []
    for m in case.requested:
        e = by_method[m]
        cleared = m in case.available
        methods.append(
            {
                **check_methods.method_view(e),
                "available": cleared and e.available,
                "reasons": list(e.reasons) + ([] if cleared else ["NOT_CLEARED_WHEN_CASE_OPENED"]),
            }
        )
    usable = [x["method"] for x in methods if x["available"]]
    return {
        "case_id": case.case_id,
        "status": case.status.value,
        "market": case.market,
        "purpose": case.purpose,
        "policy_hash_at_open": case.policy_hash,
        "policy_hash_now": live.policy_hash,
        "methods": methods,
        "usable_methods": usable,
        "message": None if usable else UNAVAILABLE_MESSAGE,
    }


async def record_assertion(
    conn: AsyncConnection,
    case_id: str,
    *,
    assertion: Assertion,
    verdict: cc.Verdict,
    method: cm.CheckMethod,
    source: str | None,
    reason_codes: Sequence[str],
    confidence: cc.ConfidenceBand | None,
    observed_at: datetime,
    expires_at: datetime | None,
    recorded_by: str,
    now: datetime,
) -> cc.AssertionRecord:
    """One piece of evidence. The case moves from ELIGIBLE to IN_PROGRESS with its first assertion."""
    case = await _case(conn, case_id, lock=True)
    record = cc.AssertionRecord(
        assertion_id=str(uuid.uuid4()), case_id=case.case_id, assertion=assertion, verdict=verdict, method=method, source=source,
        reason_codes=tuple(reason_codes), confidence=confidence, observed_at=observed_at, expires_at=expires_at,
        recorded_by=recorded_by, recorded_at=now,
    )  # fmt: skip
    cc.check_assertion(case, record, now=now)
    await store.insert_assertion(conn, case.tenant_id, record)
    if case.status is cc.CaseStatus.ELIGIBLE:
        await store.update_status(conn, cc.move(case, cc.CaseStatus.IN_PROGRESS, now=now))
    return record


async def evidence(conn: AsyncConnection, case_id: str, *, now: datetime) -> dict[str, Any]:
    case = await _case(conn, case_id)
    return cc.evidence_view(case, await store.assertions(conn, case.case_id), now=now)


async def close(conn: AsyncConnection, case_id: str, *, now: datetime) -> cc.Case:
    case = await _case(conn, case_id, lock=True)
    closed = cc.move(case, cc.CaseStatus.CLOSED, now=now)
    await store.update_status(conn, closed)
    return closed


__all__ = [
    "UNAVAILABLE_MESSAGE",
    "case_view",
    "close",
    "evidence",
    "get_case",
    "open_case",
    "recent",
    "record_assertion",
    "requirements",
]
