"""The first-verification checklist and the partner's webhook confirmation (ZID-CP-WF10 v2.0, WF-10.01; CR-173). Runs in the caller's transaction."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verify_api.domain import onboarding as ob
from zid_platform.modules.verify_api.domain.model import PartnerError
from zid_platform.modules.verify_api.infrastructure import store
from zid_platform.modules.webhook.ports import active_endpoint_count, delivered_event


async def checklist(conn: AsyncConnection, application_id: str) -> dict[str, Any]:
    app = await store.get_application(conn, application_id)
    if app is None:
        raise PartnerError("NOT_FOUND", "No such application.")
    facts = await store.onboarding_facts(conn, application_id)
    return {**ob.view({**facts, "endpoint_active": await active_endpoint_count(conn) > 0}), "application_id": application_id, "environment": str(app["environment"]).lower()}


async def confirm_webhook(conn: AsyncConnection, tenant_id: str, application_id: str, event_id: str) -> dict[str, Any]:
    """The partner's server says it received this delivered event and verified its signature. The event must be a real, delivered one of this company, and counts once."""
    delivered = await delivered_event(conn, event_id)
    if not delivered:
        raise PartnerError("NOT_FOUND", "That event was not delivered to this company, or the id is wrong.")
    if not await store.insert_confirmation(conn, tenant_id, application_id, event_id):
        problems = ob.confirmation_problems(True, True)
        raise PartnerError("VALIDATION_FAILED", "That event is already confirmed." if "ALREADY_CONFIRMED" in problems else "Refused.")
    return await checklist(conn, application_id)
