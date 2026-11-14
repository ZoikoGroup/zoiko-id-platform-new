"""Re-evaluate the verification sessions that had not finished when the policy of their region changed (conformance T40; CR-092). Worker pass.

A session records, once, the policy pack that governed it when it began (`regulatory_basis`). When the pack governing that market has changed since
(a new version approved, or the one in force retired), or the provider behind the session has been suspended (the kill switch, RCG-G08), the session
is re-evaluated against what governs NOW:
- a pack still governs, a different one: the session carries on and the change is recorded (CONTINUES_UNDER_NEW_PACK);
- no pack governs any more, or the provider is suspended: the session goes to a person (MANUAL_REVIEW) when it was already with a provider, and is
  recorded as REVIEW_REQUIRED either way. A change is never read as a pass, and never fails the person.
One notice per affected session, scoped to its own organization and region, on the outbox (`verification.session.policy_reevaluated`): ids, country,
capability, pack versions and the result, never the subject. The same change is evaluated once per session (the review key), so a repeat pass does
nothing. Only sessions of the changed market are touched: the market is the session's own recorded country and capability.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import in_tenant
from zid_outbox import emit

from zid_platform.modules.verification.application import policy_packs, suspensions
from zid_platform.modules.verification.domain import suspension as sp
from zid_platform.modules.verification.domain.policy_packs import Outcome, PackError, pack_hash
from zid_platform.modules.verification.domain.verification import (
    SessionStatus,
    VerificationError,
    transition,
)

CONTINUES = "CONTINUES_UNDER_NEW_PACK"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
PENDING = ("CREATED", "IN_PROGRESS", "PENDING_EXTERNAL", "RESUBMISSION_REQUIRED")
WITH_PROVIDER = {"IN_PROGRESS", "PENDING_EXTERNAL", "RESUBMISSION_REQUIRED"}


def pack_ref(pack: dict[str, Any] | None) -> str:
    return "NONE" if not pack else f"{pack['pack_id']}@{pack['version']}#{pack['pack_hash']}"


async def _version(conn: AsyncConnection, session_id: str) -> int:
    return int(
        (
            await conn.execute(
                text(
                    "SELECT coalesce(max(aggregate_version), 0) + 1 FROM events.outbox_event "
                    "WHERE aggregate_type = 'verification_session' AND aggregate_id = :i"
                ),
                {"i": session_id},
            )
        ).scalar_one()
    )


async def reevaluate_pending(
    conn: AsyncConnection,
    now: datetime,
    *,
    mode: str = "shadow",
    limit: int = 200,
    only_tenant: str | None = None,
) -> dict[str, int]:
    """One worker pass across tenants. `mode` is the platform's pack switch: with it off nothing is read."""
    counts = {"checked": 0, "unchanged": 0, "continues": 0, "review_required": 0}
    if mode == "off":
        return counts
    rows = (
        await conn.execute(
            text(
                "SELECT session_id, tenant_id, status, regulatory_basis, policy_review FROM verify.verification_session "
                "WHERE status = ANY(CAST(:st AS text[])) AND regulatory_basis IS NOT NULL AND expires_at > :now "
                "AND (CAST(:only AS uuid) IS NULL OR tenant_id = CAST(:only AS uuid)) ORDER BY updated_at LIMIT :n"
            ),
            {"st": list(PENDING), "now": now, "n": limit, "only": only_tenant},
        )
    ).all()
    for row in rows:
        basis: dict[str, Any] = dict(row.regulatory_basis)
        country, capability = basis.get("country"), basis.get("capability")
        if not country or not capability:
            continue
        counts["checked"] += 1
        tenant, session_id = str(row.tenant_id), str(row.session_id)
        async with in_tenant(conn, tenant):
            try:
                resolution = await policy_packs.governing_pack(
                    conn,
                    country=country,
                    product=capability,
                    purpose=basis.get("purpose") or "",
                    at=now,
                )
            except PackError:
                resolution = None
            current = (
                {
                    "pack_id": resolution.pack.pack_id,
                    "version": resolution.pack.version,
                    "pack_hash": pack_hash(resolution.pack),
                }
                if resolution is not None
                and resolution.outcome is Outcome.APPLICABLE
                and resolution.pack is not None
                else None
            )
            suspended: list[str] = []
            provider = basis.get("provider")
            if provider:
                stopped = await suspensions.blocking_for(
                    conn,
                    sp.Invocation(
                        provider=provider,
                        service_id=capability,
                        country=str(country).upper(),
                        purpose=basis.get("purpose"),
                    ),
                    at=now,
                )
                suspended = sorted(s.suspension_id for s in stopped)
            key = pack_ref(current) + ("|SUSPENDED" if suspended else "")
            seen = (row.policy_review or {}).get("key") or pack_ref(basis.get("pack"))
            if key == seen:
                counts["unchanged"] += 1
                continue
            result = REVIEW_REQUIRED if (current is None or suspended) else CONTINUES
            moved = False
            if result == REVIEW_REQUIRED and row.status in WITH_PROVIDER:
                try:
                    transition(SessionStatus(row.status), SessionStatus.MANUAL_REVIEW)
                    moved = True
                except VerificationError:
                    moved = False
            review = {
                "key": key,
                "result": result,
                "started_under": pack_ref(basis.get("pack")),
                "governs_now": pack_ref(current),
                "provider_suspended": bool(suspended),
                "reviewed_at": now.isoformat(),
            }
            move = ", status = 'MANUAL_REVIEW', updated_at = :now, row_version = row_version + 1"
            await conn.execute(
                text(
                    "UPDATE verify.verification_session SET policy_review = CAST(:r AS jsonb)"
                    + (move if moved else "")
                    + " WHERE session_id = :s"
                ),
                {"r": json.dumps(review, sort_keys=True), "s": row.session_id, "now": now},
            )
            await emit(
                conn,
                name="verification.session.policy_reevaluated",
                tenant_id=tenant,
                aggregate_type="verification_session",
                aggregate_id=session_id,
                aggregate_version=await _version(conn, session_id),
                data={
                    "session_id": session_id,
                    "country": str(country).upper(),
                    "capability": capability,
                    "result": result,
                    "started_under": review["started_under"],
                    "governs_now": review["governs_now"],
                    "provider_suspended": bool(suspended),
                    "status": "MANUAL_REVIEW" if moved else str(row.status),
                },
            )
            counts["review_required" if result == REVIEW_REQUIRED else "continues"] += 1
    return counts
