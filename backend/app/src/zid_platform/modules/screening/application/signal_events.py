"""The platform event a raised signal publishes (CR-068; ZID-GAP-001 WP-14).

Kept apart from the signal rules on purpose: this is the only file in the screening application layer that writes to the outbox, so the
event it emits is the only dotted name the event-catalogue guard has to see here. The payload is ids and codes only: no hash, count or name.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit, next_version

from zid_platform.modules.screening.infrastructure import signal_store as store


async def publish_signal_raised(
    conn: AsyncConnection, tenant_id: str, review: store.ReviewRow
) -> None:
    """Tell the platform a review item is waiting for a person: one outbox event in the caller's transaction."""
    await emit(
        conn,
        name="screening.signal.raised",
        tenant_id=tenant_id,
        aggregate_type="screening_signal",
        aggregate_id=review.review_id,
        aggregate_version=await next_version(conn, "screening_signal", review.review_id),
        data={
            "review_id": review.review_id,
            "signal_source": review.source,
            "rule": review.rule,
            "risk_tier": review.risk_tier,
        },
    )


async def publish_signal_corrected(
    conn: AsyncConnection, tenant_id: str, review: store.ReviewRow
) -> None:
    """Tell the platform a person CLEARED a signal that was raised earlier (ZID-PRI-004 AT-019: a corrected link is reviewed and superseded).

    Whatever relied on the earlier `screening.signal.raised` event for this review id must set that reliance aside and decide again. The payload is
    ids and codes only: no rationale, no evidence text, no key."""
    await emit(
        conn,
        name="screening.signal.corrected",
        tenant_id=tenant_id,
        aggregate_type="screening_signal",
        aggregate_id=review.review_id,
        aggregate_version=await next_version(conn, "screening_signal", review.review_id),
        data={
            "review_id": review.review_id,
            "supersedes_event": "screening.signal.raised",
            "signal_source": review.source,
            "rule": review.rule,
            "outcome": "CLEARED_BY_REVIEWER",
            "action_required": "RECOMPUTE_DECISIONS_THAT_RELIED_ON_THIS_SIGNAL",
        },
    )
