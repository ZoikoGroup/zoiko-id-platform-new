"""Reading and changing tenant settings, and the enforced effects (ZID-UX-001 §19; CR-022).

A change names the revision it was based on, is validated against a closed vocabulary, is appended to the
history, bumps the tenant's configuration revision (so it shows in the change history) and leaves an audit
record. What each area *does* is enforced where it matters, not just stored:
- AUTHENTICATION shortens new and existing sessions (session_limits, used by sign-in and by every request);
- NOTIFICATIONS mutes non-mandatory alert kinds before they are raised (muted_alert_kinds);
- BRANDING is shown on the workspace's sign-in page (public_branding).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.configuration.domain.settings import AREAS, SettingsError, validate
from zid_platform.modules.configuration.infrastructure import snapshot_store as store


async def get_all(conn: AsyncConnection) -> dict[str, Any]:
    return {area: await store.current(conn, area) for area in AREAS}


async def update(
    conn: AsyncConnection,
    tenant_id: str,
    area: str,
    *,
    expected_revision: int,
    value: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    # Imported here, not at module level: the session module reads session_limits on every request, and these
    # modules (tenant, evidence, notification) import the session module themselves.
    from zid_platform.modules.evidence.ports import (
        AuditFacts,
        EvidenceClass,
        material_commitment,
        record_audit,
    )
    from zid_platform.modules.notification.ports import MANDATORY_KINDS, TEMPLATES
    from zid_platform.modules.tenant.ports import bump_configuration

    clean = validate(area, value, mandatory_kinds=MANDATORY_KINDS, known_kinds=frozenset(TEMPLATES))
    now = datetime.now(UTC)
    commit = material_commitment({"tenant": tenant_id, "area": area, "value": clean})
    snapshot = await store.write(
        conn,
        tenant_id,
        area,
        expected_revision=expected_revision,
        value=clean,
        commitment=commit,
        actor=actor,
        now=now,
    )
    await bump_configuration(
        conn,
        tenant_id,
        change_kind=f"settings.{area.lower()}",
        change_commitment=commit,
        actor_principal_id=actor,
        approval_ref=None,
        now=now,
        security=area == "AUTHENTICATION",
    )
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type=f"configuration_settings.{area.lower()}_changed",
            actor_type="HUMAN",
            action="changed",
            target_type="tenant_settings",
            target_ref=area,
            outcome="SUCCESS",
            evidence_class=EvidenceClass.E2,
            occurred_at=now,
            actor_principal_id=actor,
            source_service="configuration",
            source_event_id=None,
            details={"area": area, "revision": snapshot["revision"]},
        ),
    )
    return snapshot


# ---------------------------------------------------------------- enforced effects (used by other modules via ports)


async def session_limits(conn: AsyncConnection) -> tuple[timedelta | None, timedelta | None]:
    """(absolute lifetime, idle timeout) the tenant has chosen, each None when it keeps the platform's."""
    value = (await store.current(conn, "AUTHENTICATION"))["value"]
    hours, minutes = value.get("session_absolute_hours"), value.get("session_idle_minutes")
    return (
        timedelta(hours=hours) if hours else None,
        timedelta(minutes=minutes) if minutes else None,
    )


async def session_expiry(conn: AsyncConnection, now: datetime, platform_ttl: timedelta) -> datetime:
    """When a session created `now` ends: the platform lifetime, shortened by the tenant's choice."""
    absolute, _idle = await session_limits(conn)
    return now + (min(platform_ttl, absolute) if absolute else platform_ttl)


async def muted_alert_kinds(conn: AsyncConnection) -> frozenset[str]:
    return frozenset((await store.current(conn, "NOTIFICATIONS"))["value"].get("muted_kinds", []))


async def public_branding(conn: AsyncConnection) -> dict[str, str]:
    """Only presentation fields, safe to show before sign-in."""
    value = (await store.current(conn, "BRANDING"))["value"]
    return {k: str(value.get(k, "")) for k in ("display_name", "accent", "support_url")}


__all__ = ["SettingsError"]
