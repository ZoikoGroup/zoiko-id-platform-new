"""Verify API usage: what a company used, and the usage facts the platform records (CR-154; ZID-EXT-IDV-001 epic E17, WF-15, QA Q38).

Two separate things, kept apart on purpose:
- The REPORT is read from the company's own sessions and decisions, month by month, per application, with sandbox and production never mixed (a sandbox test is never usage).
- The LEDGER entries are written only for PRODUCTION: one `VERIFY_SESSION_START` when a session starts and one `VERIFY_CHECK_COMPLETE` when it reaches a final decision, each with a unique key so a retry or a
  revised decision never counts twice. They are usage facts only: nothing is rated or invoiced until the owners ratify the billable-event policy (D12) and approve a price, and the report says so.

Recording never blocks a verification: the ledger write never raises (the platform repairs billing by reconciliation, and this module shows the reconciliation so a gap is seen, not hidden).
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.ports import (
    MINOR_UNITS,
    billable_store,
    key_verify_check_complete,
    key_verify_session_start,
    record_billable,
)
from zid_platform.modules.verify_api.application import pricing_service
from zid_platform.modules.verify_api.domain import model as m
from zid_platform.modules.verify_api.domain import pricing

MONTH = re.compile(r"^(20\d{2})-(0[1-9]|1[0-2])$")
BILLING_NOTE = (
    "These are usage counts only. Nothing is priced or invoiced yet: the billing policy for verifications has not been approved. "
    "Sandbox tests are shown separately and are never usage."
)
COST_NOTE = (
    "This cost is indicative: production usage multiplied by the price Zoiko iD has approved, shown with the price used on every line. It is not an invoice and includes no tax. "
    "A currency marked incomplete has usage with no approved price yet, so its total is not the full amount."
)
FINAL_OUTCOMES = ("verified", "not_verified")


def check_month(value: str | None, now: datetime | None = None) -> date:
    """The first day of the month asked for (this month when none), or the reason it is not a month."""
    if value is None:
        n = now or datetime.now(UTC)
        return date(n.year, n.month, 1)
    hit = MONTH.fullmatch(value)
    if hit is None:
        raise m.PartnerError("VALIDATION_FAILED", "Use a month like 2026-11.")
    return date(int(hit.group(1)), int(hit.group(2)), 1)


# ------------------------------------------------------------------------------- the ledger (production only)


async def meter_session_started(conn: AsyncConnection, row: dict[str, Any]) -> bool:
    """One usage fact for a production session that started. A sandbox session writes nothing. Never raises."""
    if row["environment"] != "PRODUCTION":
        return False
    return await record_billable(
        conn,
        tenant_id=str(row["tenant_id"]),
        meter="VERIFY_SESSION_START",
        key=key_verify_session_start(row["session_id"]),
        at=row["created_at"] if isinstance(row.get("created_at"), datetime) else None,
        principal_class="NON_HUMAN",
        origin="API",
        transaction_type="SUBMITTED",
        billable_result="NO_CHARGE",
        finality="PENDING",
    )


async def meter_check_completed(
    conn: AsyncConnection, row: dict[str, Any], outcome: str, at: datetime
) -> bool:
    """One usage fact for a production session that reached a FINAL decision (verified or not verified). A review or an inconclusive result is not final and writes nothing; a later revision of a
    final decision is the same session and the same key, so it is never a second event. Never raises."""
    if row["environment"] != "PRODUCTION" or outcome not in FINAL_OUTCOMES:
        return False
    return await record_billable(
        conn,
        tenant_id=str(row["tenant_id"]),
        meter="VERIFY_CHECK_COMPLETE",
        key=key_verify_check_complete(row["session_id"]),
        at=at,
        principal_class="NON_HUMAN",
        origin="API",
        transaction_type="DELIVERED",
        billable_result="CHARGEABLE",
        finality="PROVISIONAL",
    )


# ------------------------------------------------------------------------------- the report


async def report(conn: AsyncConnection, month_start: date) -> dict[str, Any]:
    """Per application and environment: sessions started in the month and where they stand now, by the session's latest decision. Counts only."""
    nxt = date(month_start.year + (month_start.month == 12), month_start.month % 12 + 1, 1)
    rows = (
        await conn.execute(
            text(
                "SELECT a.application_id, a.name, s.environment, "
                "count(*) AS started, "
                "count(*) FILTER (WHERE d.outcome = 'verified') AS verified, "
                "count(*) FILTER (WHERE d.outcome = 'not_verified') AS not_verified, "
                "count(*) FILTER (WHERE d.outcome = 'inconclusive') AS inconclusive, "
                "count(*) FILTER (WHERE d.outcome = 'review_required') AS review_required, "
                "count(*) FILTER (WHERE s.status = 'canceled') AS canceled, "
                "count(*) FILTER (WHERE s.status = 'expired') AS expired "
                "FROM verify.api_session s JOIN verify.api_application a USING (application_id) "
                "LEFT JOIN LATERAL (SELECT outcome FROM verify.api_decision WHERE session_id = s.session_id ORDER BY decision_version DESC LIMIT 1) d ON true "
                "WHERE s.created_at >= :a AND s.created_at < :b "
                "GROUP BY a.application_id, a.name, s.environment ORDER BY s.environment DESC, a.name"
            ),
            {"a": month_start, "b": nxt},
        )
    ).all()
    items = [
        {
            "application_id": str(r.application_id),
            "application_name": r.name,
            "environment": r.environment.lower(),
            "sessions_started": int(r.started),
            "verified": int(r.verified),
            "not_verified": int(r.not_verified),
            "inconclusive": int(r.inconclusive),
            "review_required": int(r.review_required),
            "canceled": int(r.canceled),
            "expired": int(r.expired),
        }
        for r in rows
    ]
    totals: dict[str, dict[str, int]] = {"sandbox": {}, "production": {}}
    for env, bucket in totals.items():
        mine = [i for i in items if i["environment"] == env]
        for k in (
            "sessions_started",
            "verified",
            "not_verified",
            "inconclusive",
            "review_required",
            "canceled",
            "expired",
        ):
            bucket[k] = sum(i[k] for i in mine)
    reconciliation = await _reconcile(conn, month_start, totals["production"])
    cost = pricing.indicative_cost(
        await pricing_service.approved(conn),
        {
            "VERIFY_SESSION_START": totals["production"]["sessions_started"],
            "VERIFY_CHECK_COMPLETE": reconciliation["checks_completed"]["sessions"],
        },
        month_start=month_start,
        minor_units=MINOR_UNITS,
    )
    return {
        "month": f"{month_start:%Y-%m}",
        "billing": {"state": "INDICATIVE_COST", "note": COST_NOTE, "cost": cost}
        if cost
        else {"state": "USAGE_FACTS_ONLY", "note": BILLING_NOTE, "cost": []},
        "totals": totals,
        "items": items,
        "reconciliation": reconciliation,
    }


async def _reconcile(
    conn: AsyncConnection, month_start: date, production: dict[str, int]
) -> dict[str, Any]:
    """Production sessions against the ledger: every started session and every final decision should have exactly one usage fact. A difference is shown, never hidden (Q38)."""
    ledger = await billable_store.event_totals(conn, month_start)
    started = int(ledger.get("VERIFY_SESSION_START", (0, Decimal(0)))[0])
    completed = int(ledger.get("VERIFY_CHECK_COMPLETE", (0, Decimal(0)))[0])
    nxt = date(month_start.year + (month_start.month == 12), month_start.month % 12 + 1, 1)
    # a session counts once, in the month its FIRST final decision was made (the ledger's rule), whatever month it started in
    final = int(
        (
            await conn.execute(
                text(
                    "SELECT count(*) FROM verify.api_decision d JOIN verify.api_session s USING (session_id) "
                    "WHERE s.environment = 'PRODUCTION' AND d.outcome IN ('verified', 'not_verified') AND d.completed_at >= :a AND d.completed_at < :b "
                    "AND NOT EXISTS (SELECT 1 FROM verify.api_decision e WHERE e.session_id = d.session_id AND e.outcome IN ('verified', 'not_verified') AND e.decision_version < d.decision_version)"
                ),
                {"a": month_start, "b": nxt},
            )
        ).scalar_one()
    )
    return {
        "sessions_started": {"sessions": production["sessions_started"], "ledger": started},
        "checks_completed": {"sessions": final, "ledger": completed},
        "reconciled": started == production["sessions_started"] and completed == final,
    }
