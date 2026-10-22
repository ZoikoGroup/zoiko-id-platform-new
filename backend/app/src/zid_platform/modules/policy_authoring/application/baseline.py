"""The platform baseline policy release seeded into a new tenant (TENANCY-001 §14 step 7; G-W1).

Decisions stay authority-driven: the baseline permits an action exactly when a valid authority chain
covers it (the PDP computes `authority_valid` from grants and roots), so the release adds no power of
its own; the platform floor (backend/policies/cedar/platform-floor) still applies on top. It is
published through the ordinary release gates (cases run, publishability check, review) under the
platform's own author and reviewer identities, and never replaces a release the tenant already has.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7

from zid_platform.modules.policy_authoring.infrastructure import release_store
from zid_platform.modules.policy_runtime.ports import (
    PolicyCase,
    PolicyRelease,
    ReleaseStatus,
)

BASELINE = (
    '@id("tenant-baseline") permit (principal, action, resource) when { context.authority_valid };'
)
PLATFORM_AUTHOR = "00000000-0000-7000-8000-00000000ba5e"  # platform policy publication identity
PLATFORM_REVIEWER = "00000000-0000-7000-8000-00000000ba5f"
_OK = {
    "tenant_match": True,
    "authority_valid": True,
    "authority_stale": False,
    "security_state": "NORMAL",
    "risk_class": 3,
    "purpose": "",
}


async def seed_baseline_release(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    example_action: str,
    catalog: Mapping[str, Mapping[str, Any]],
    now: datetime,
) -> str | None:
    """Returns the new release id, or None when the tenant already has an ACTIVE release."""
    if await release_store.active_release(conn, tenant_id) is not None:
        return None
    rid = str(uuid7())
    await release_store.create_release(
        conn,
        PolicyRelease(
            rid,
            tenant_id,
            BASELINE,
            "1",
            ReleaseStatus.DRAFT,
            (
                PolicyCase("authority permits", "p", example_action, "r", _OK, True),
                PolicyCase(
                    "no authority denies",
                    "p",
                    example_action,
                    "r",
                    {**_OK, "authority_valid": False},
                ),
            ),
            author=PLATFORM_AUTHOR,
        ),
        policy_name="tenant-baseline",
        version=1,
    )
    for target in (ReleaseStatus.VALIDATING, ReleaseStatus.REVIEW):
        await release_store.move(conn, rid, target, catalog=catalog, now=now)
    await release_store.add_reviewer(conn, rid, PLATFORM_REVIEWER)
    await release_store.move(conn, rid, ReleaseStatus.ACTIVE, catalog=catalog, now=now)
    return rid
