"""The partner gate in front of a partner-originated operation (ZID-COM-WF-2026-11 WF-I06; INV-09; CAT-34, CAT-35).

`authorise` decides whether a partner may act for a child here and now, and who the one bill-to is. `record_event` then records the operation through the ordinary
recorder with that payer and the partner named, so a partner call is billed once, to the payer the signed agreement names, and never skips a check the platform
already makes for any call: this gate is added to those, it replaces none of them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.application import recorder
from zid_platform.modules.commercial.domain import partner as rules
from zid_platform.modules.commercial.domain.catalogue import CommercialError
from zid_platform.modules.commercial.infrastructure import partner_store


async def authorise(
    conn: AsyncConnection,
    *,
    partner_id: str,
    child_tenant_id: str,
    country: str | None,
    now: datetime | None = None,
) -> str:
    """The legal payer for this operation, or a refusal that names every reason. An unknown partner, child, country or term is a refusal, never a pass."""
    row = await partner_store.get(conn, partner_id)
    partner = partner_store.model(row) if row else None
    child = await partner_store.load_child(conn, partner_id, child_tenant_id) if row else None
    try:
        rules.check(partner, child, country=country, now=now or datetime.now(UTC))
    except rules.PartnerError as e:
        raise CommercialError(e.code, e.detail) from e
    assert partner is not None
    return rules.legal_payer(partner, child_tenant_id)


async def record_event(
    conn: AsyncConnection,
    *,
    partner_id: str,
    child_tenant_id: str,
    country: str | None,
    meter: str,
    key: str,
    quantity: Decimal | int = 1,
    at: datetime | None = None,
) -> bool:
    """Record one partner-originated operation for the child. True when this call created the event; a retry of the same operation is recorded once."""
    payer = await authorise(
        conn, partner_id=partner_id, child_tenant_id=child_tenant_id, country=country, now=at
    )
    return await recorder.record(
        conn,
        tenant_id=child_tenant_id,
        meter=meter,
        key=key,
        quantity=quantity,
        at=at,
        origin="PARTNER",
        partner_id=partner_id,
        legal_payer_id=payer,
    )
