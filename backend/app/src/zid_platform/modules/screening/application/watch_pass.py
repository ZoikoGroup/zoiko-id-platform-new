"""The scheduled pass for ongoing screening watches (ZID-DTV-WF-06 S12, ENG-14; CR-113).

For each tenant with an active watch, screen again every watch whose cadence has run out for any source. A watch is held, and counted as held, where ongoing monitoring is not approved for its market
(the monitoring scope check of CR-081 applies to this too: there is no background monitoring by default). One watch failing never stops another, and a failed run records an attempt and no success, so
the watch stops reading as continuously monitored once its last success is older than its cadence.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import in_tenant

from zid_platform.modules.screening.application import screening_requests, watches
from zid_platform.modules.screening.domain import eligibility as elig
from zid_platform.modules.screening.infrastructure import store
from zid_platform.modules.screening.infrastructure.config import ScreeningConfig
from zid_platform.modules.screening.infrastructure.market_policies import MarketPack

log = logging.getLogger("zid.screening")


async def watch_pass(
    conn: AsyncConnection,
    cfg: ScreeningConfig,
    now: datetime,
    *,
    packs: dict[str, MarketPack],
    identity_lookup: Callable[..., Any],
    monitoring_gate: Callable[[str], None] | None = None,
    gate_check: Callable[..., Any] | None = None,
    registry: Any = None,
    adapters: Any = None,
) -> dict[str, int]:
    """tenant id -> watches screened again; `tenant id:held_by_monitoring_scope` -> watches left alone because no approval covers their market; `tenant id:failed` -> runs that raised."""
    tenants = [
        str(r.tenant_id)
        for r in (
            await conn.execute(
                text("SELECT DISTINCT tenant_id FROM verify.screening_watch WHERE active")
            )
        ).all()
    ]
    out: dict[str, int] = {}
    for tenant in tenants:
        done = held = failed = unreleased = 0
        async with in_tenant(conn, tenant):
            snapshots = await store.snapshots(conn)
            for watch_id in await watches.due_watches(conn, tenant, now=now):
                row = await watches._load(conn, watch_id)
                case = await screening_requests._row(conn, str(row.request_id))
                if case is None:
                    continue
                if monitoring_gate is not None:
                    try:
                        monitoring_gate(case.market)
                    except ValueError:  # not approved for ongoing monitoring: held, never screened
                        held += 1
                        log.warning(
                            "watch %s held: no monitoring approval covers market %s",
                            watch_id,
                            case.market,
                        )
                        continue
                if gate_check is not None and not await gate_check(
                    conn, case.market
                ):  # ongoing monitoring is released per market: the release gates must be signed
                    unreleased += 1
                    log.warning(
                        "watch %s held: the release gates for market %s are not all signed",
                        watch_id,
                        case.market,
                    )
                    continue
                claim = await identity_lookup(
                    conn, tenant, elig.SubjectType(case.subject_type), case.subject_ref, now
                )
                identity = None if claim is None else elig.IdentityClaim(claim[0], claim[1], tenant)
                try:
                    async with conn.begin_nested():
                        await watches.run_watch(
                            conn, watch_id, pack=packs.get(case.market), snapshots=snapshots, max_age=cfg.policy.list_max_age, match_policy=cfg.policy.match,
                            identity=identity, now=now, registry=registry, adapters=adapters,
                        )  # fmt: skip
                    done += 1
                except Exception:
                    failed += 1
                    log.exception("screening watch %s failed", watch_id)
        out[tenant] = done
        if held:
            out[f"{tenant}:held_by_monitoring_scope"] = held
        if failed:
            out[f"{tenant}:failed"] = failed
        if unreleased:
            out[f"{tenant}:held_by_release_gates"] = unreleased
    return out
