"""Scheduled work for company verification (ZID-ED-03 section 08; WF-13B AT-11): look again before a verification lapses.

`refresh_pass` reads the sources again for every case whose verification lapses within the lead time (or already has) and appends the
new decision. It asks the country gate first: a country that is no longer open is left alone, never refreshed and never marked
verified. A source outage never replaces a verification that still stands. One case failing never stops another. It runs under the
worker role, which sees every tenant, and returns a count per tenant for logs and tests.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import in_tenant
from zid_errors import ZidError

from zid_platform.modules.organization.application import kyb
from zid_platform.modules.organization.domain.kyb import KybError, Routing
from zid_platform.modules.organization.infrastructure.kyb_adapters import KybAdapter

log = logging.getLogger("zid.kyb")


async def refresh_pass(
    conn: AsyncConnection,
    adapters: Mapping[str, KybAdapter],
    routing: Routing,
    sign: kyb.Signer,
    now: datetime,
    *,
    country_gate: kyb.CountryGate | None = None,
    lead: timedelta | None = None,
) -> dict[str, int]:
    """`lead` defaults to the policy's refresh lead.
    tenant id -> cases refreshed; `tenant id:held_by_country` -> cases left alone because their country is not open."""
    due = (
        await conn.execute(
            text(
                "SELECT tenant_id, case_id, jurisdiction FROM org.kyb_case "
                "WHERE status = 'VERIFIED' AND expiry_at IS NOT NULL AND expiry_at <= :horizon "
                "ORDER BY tenant_id, expiry_at"
            ),
            {"horizon": now + (lead if lead is not None else routing.policy.refresh_lead)},
        )
    ).all()
    out: dict[str, int] = {}
    for row in due:
        tenant, case_id = str(row.tenant_id), str(row.case_id)
        if country_gate is not None:
            try:
                country_gate(str(row.jurisdiction))
            except (ValueError, ZidError):
                key = f"{tenant}:held_by_country"
                out[key] = out.get(key, 0) + 1
                log.warning("refresh of company case %s held: its country is not open", case_id)
                continue
        try:
            async with conn.begin_nested(), in_tenant(conn, tenant):
                await kyb.refresh(
                    conn, tenant, case_id,
                    adapters=adapters, routing=routing, sign=sign, now=now,
                    country_gate=country_gate, keep_valid_on_outage=True,
                )  # fmt: skip
            out[tenant] = out.get(tenant, 0) + 1
        except (KybError, ZidError):
            log.exception("refresh of company case %s failed", case_id)
        except Exception:
            log.exception("refresh of company case %s failed unexpectedly", case_id)
    return out
