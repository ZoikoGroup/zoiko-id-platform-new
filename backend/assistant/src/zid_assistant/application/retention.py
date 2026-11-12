"""Retention and erasure jobs (PRI-009 section 04, RET-*). Periods are CONFIGURATION, proposals until Privacy signs them.

There is no raw-chat dataset to purge (RET-01: zero persistent days). Sessions expire on their own in the session store (RET-02).
What this job removes: feedback and message metrics, and security detection metadata, after their configured windows. Intake data
(RET-10) has no automatic purge: it is disabled until a signed schedule exists, and is erased only through `erase_intake`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from zid_assistant.infrastructure.store import Store
from zid_assistant.settings import AssistantSettings


async def purge_expired(
    store: Store, settings: AssistantSettings, now: datetime | None = None
) -> dict[str, int]:
    return await store.purge(
        now or datetime.now(UTC), settings.retain_feedback_days, settings.retain_safety_event_days
    )


async def erase_intake(store: Store, reference: str) -> bool:
    """Rights-driven erasure by opaque reference: nulls the encrypted fields and tombstones the row. Called by the privacy
    process (never by a public route: there is no anonymous lookup by email, INT-010 section 05)."""
    done = await store.erase_intake(reference)
    if done:
        await store.audit("INTAKE_ERASED", None, "privacy-process", None, "ERASED")
    return done
