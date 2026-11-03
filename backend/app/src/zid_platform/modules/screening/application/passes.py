"""Scheduled work for screening (ZID-ED-04 section 07): keep the lists current and screen again when they change.

`ingest_pass` fetches every list that has a verified address and loads it; one list failing never stops another, and a failure
never changes the list in use (it ages, and a list that is too old makes screening UNAVAILABLE). `rescreen_pass` screens again
every case whose last decision no longer reflects the lists, or whose clear outcome has lapsed, and appends the new decision.
Both run under the worker role and return a code per list or a count per tenant for logs and tests.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import in_tenant
from zid_errors import ZidError

from zid_platform.modules.screening.application import screening
from zid_platform.modules.screening.domain.decision import ScreeningError
from zid_platform.modules.screening.infrastructure.config import ScreeningConfig
from zid_platform.modules.screening.infrastructure.fetch import FetchError, fetch_source

log = logging.getLogger("zid.screening")
Signer = Callable[[dict[str, Any]], str]


async def ingest_pass(
    conn: AsyncConnection,
    cfg: ScreeningConfig,
    now: datetime,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, str]:
    """list id -> LOADED | UNCHANGED | NO_ADDRESS | a fetch or format error code."""
    out: dict[str, str] = {}
    for list_id, source in cfg.sources.items():
        if not source.configured:
            out[list_id.value] = "NO_ADDRESS"
            continue
        try:
            files = await fetch_source(source, transport=transport)
        except FetchError as e:
            out[list_id.value] = e.code
            log.warning("sanctions list %s not fetched: %s", list_id.value, e.code)
            continue
        try:
            async with conn.begin_nested():  # a refused file undoes only itself
                out[list_id.value] = await screening.ingest_list(
                    conn,
                    list_id,
                    files,
                    fetched_at=now,
                    source_ref=",".join(sorted(source.urls.values()))[:500],
                    now=now,
                )
        except ScreeningError as e:
            out[list_id.value] = e.code
            log.warning("sanctions list %s refused: %s", list_id.value, e.code)
    return out


async def rescreen_pass(
    conn: AsyncConnection,
    cfg: ScreeningConfig,
    sign: Signer,
    now: datetime,
    *,
    country_gate: Callable[[str], None] | None = None,
) -> dict[str, int]:
    """tenant id -> how many cases were screened again; `tenant id:held_by_country` -> how many were left alone because the
    case's country is no longer open for business verification (ZID-EDR-001: country permissions hold in jobs too). A held case
    keeps its last decision, which ages out on its own, and is looked at again on the next pass; nothing is screened, appended
    or marked verified for a country that has been closed."""
    tenants = [
        str(r.tenant_id)
        for r in (
            await conn.execute(text("SELECT DISTINCT tenant_id FROM verify.screening_case"))
        ).all()
    ]
    out: dict[str, int] = {}
    for tenant in tenants:
        done = held = 0
        async with in_tenant(conn, tenant):
            due = await screening.due_for_rescreen(conn, tenant, now=now)
            countries = await screening.case_countries(conn, due)
            for case_id in due:
                if country_gate is not None:
                    try:
                        country_gate(countries.get(case_id, ""))
                    except (
                        ValueError,
                        ZidError,
                    ):  # a closed, unknown or unreadable country is a hold, never a screening
                        held += 1
                        log.warning("rescreen of case %s held: its country is not open", case_id)
                        continue
                try:
                    async with conn.begin_nested():
                        await screening.rescreen(conn, tenant, case_id, cfg=cfg, sign=sign, now=now)
                    done += 1
                except Exception:
                    log.exception("rescreen of case %s failed", case_id)
        out[tenant] = done
        if held:
            out[f"{tenant}:held_by_country"] = held
    return out
