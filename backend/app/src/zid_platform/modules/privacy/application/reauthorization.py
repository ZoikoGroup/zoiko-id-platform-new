"""Scheduled reauthorization of biometric evidence (ZID-DTV-WF-04 BIO-20; CR-110). A worker pass over every tenant.

    DPIA packages   an APPROVED package whose approval ends within REASSESSMENT_WINDOW (or has ended) is flagged once: an outbox event
                    `privacy.dpia.reassessment_due` and an audit record, so the controller is told before the biometric method stops. The release
                    gate already refuses an expired package at every session; this pass only warns ahead of it.
    Models          an ACTIVE model whose latest evaluation is stale (or missing) is HELD with the reason, like a drift hold: nothing new is decided
                    with it until a new evaluation is recorded and the version is promoted again. A biometric method on it stops at once.

Nothing here invents a date or a threshold: the window is engineering's (PROVISIONAL) and the evaluation validity is the model rules'.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.privacy.application import service as v1
from zid_platform.modules.privacy.domain import models as rules

REASSESSMENT_WINDOW = timedelta(days=30)  # PROVISIONAL (CR-110): the owners may change it


def due_for_reassessment(
    approved_until: datetime | None, flagged_at: datetime | None, *, now: datetime
) -> bool:
    """Pure: flag once, when the approval is within the window or already over, and not flagged since the package was approved."""
    if approved_until is None or flagged_at is not None:
        return False
    return approved_until - now <= REASSESSMENT_WINDOW


async def reauthorization_pass(conn: AsyncConnection, *, now: datetime) -> dict[str, Any]:
    flagged = 0
    for p in (
        await conn.execute(
            text(
                "SELECT package_id, tenant_id, market, product, version, approved_until, reassessment_flagged_at "
                "FROM privacy.dpia_package WHERE state = 'APPROVED' AND reassessment_flagged_at IS NULL AND approved_until <= :edge"
            ),
            {"edge": now + REASSESSMENT_WINDOW},
        )
    ).all():
        if not due_for_reassessment(p.approved_until, p.reassessment_flagged_at, now=now):
            continue
        await conn.execute(
            text(
                "UPDATE privacy.dpia_package SET reassessment_flagged_at = :now WHERE package_id = :p"
            ),
            {"now": now, "p": p.package_id},
        )
        await v1._event(
            conn,
            str(p.tenant_id),
            "privacy.dpia.reassessment_due",
            ("dpia_package", str(p.package_id)),
            {
                "package_id": str(p.package_id),
                "market": p.market,
                "product": p.product,
                "version": p.version,
                "approved_until": p.approved_until.isoformat(),
                "expired": p.approved_until <= now,
            },
            actor=None,
            version=10,  # after the package's own lifecycle events
        )
        flagged += 1

    held = 0
    for m in (
        await conn.execute(
            text(
                "SELECT v.model_id, v.tenant_id, v.model_ref, v.version, "
                "(SELECT max(e.created_at) FROM privacy.model_evaluation e WHERE e.model_id = v.model_id) AS evaluated_at "
                "FROM privacy.model_version v WHERE v.state = 'ACTIVE'"
            )
        )
    ).all():
        if m.evaluated_at is not None and now - m.evaluated_at <= rules.EVALUATION_VALID:
            continue
        why = "EVALUATION_STALE" if m.evaluated_at is not None else "EVALUATION_MISSING"
        await conn.execute(
            text(
                "UPDATE privacy.model_version SET state = 'HELD', state_reason = :why WHERE model_id = :m AND state = 'ACTIVE'"
            ),
            {"why": f"reauthorization: {why}", "m": m.model_id},
        )
        await v1._audit(
            conn,
            str(m.tenant_id),
            None,
            "privacy.model.reauthorization_held",
            actor=None,
            target_type="model_version",
            target_ref=f"{m.model_ref}@{m.version}",
            details={"reason": why},
        )
        held += 1
    return {"dpia_flagged": flagged, "models_held": held}
