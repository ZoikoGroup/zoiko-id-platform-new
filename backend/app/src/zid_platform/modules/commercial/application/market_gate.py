"""The market gate in front of a live paid service call (ZID-COM-WF-2026-11 section 10.1, INV-01, safety stop).

A platform service asks, before it runs a paid operation, whether this family may be sold here: subject country, payer country and processing region are each judged
against the ACTIVE market matrix, rights expiry and rollout stage, exactly as the configuration check does. The answer follows the commercial enforcement mode the
entitlement gate already uses: in `shadow` and `off` a refusal is reported and logged but not blocking; in `enforce` it is a 403 that names the causes. The company's
own mode can be stricter than the deployment's, never looser.

Nothing here replaces the identity, consent, residency or provider checks a call already goes through; it is added to them. An unknown fact is a refusal in every
mode: shadow only decides whether the refusal blocks, not whether it is recorded.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_errors import ErrorDetail, ZidError

from zid_platform.modules.commercial.domain import market_availability as availability
from zid_platform.modules.commercial.domain import market_matrix as matrix_rules
from zid_platform.modules.commercial.infrastructure import market_matrix_store
from zid_platform.modules.commercial.infrastructure.enforcement_store import company_mode
from zid_platform.modules.commercial.infrastructure.family_registry import family_registry

log = logging.getLogger("zid.commercial.market_gate")

STATS: Counter[str] = Counter()


async def check(
    conn: AsyncConnection,
    *,
    family: str,
    subject_country: str | None,
    payer_country: str | None,
    operating_region: str | None,
    enforcement: str = "shadow",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Decide one paid call. Returns the decision; raises MARKET_NOT_RELEASED (403) only in `enforce` mode."""
    at = now or datetime.now(UTC)
    registry = family_registry()
    fam = registry.get(family)
    mode = await company_mode(conn, enforcement)
    if fam is None:  # an unknown family is never sold, in any mode
        STATS["unknown_family"] += 1
        raise ZidError("FAMILY_UNKNOWN", f"Unknown service family: {family}.", http_status=422)
    matrix, rows = await market_matrix_store.active_rows(conn, at)
    facts = matrix_rules.facts_by_dimension(
        rows,
        registry,
        family,
        subject_country=subject_country,
        payer_country=payer_country,
        operating_region=operating_region,
        on=at.date(),
    )
    verdict = availability.decide(fam, facts)
    out: dict[str, Any] = {
        "family": family,
        "sellable": verdict.sellable,
        "mode": mode,
        "market_matrix_version": matrix["version"] if matrix else None,
        "causes": list(verdict.causes),
        "refusals": [
            {"code": r.code, "cause": r.cause.value, "dimension": r.dimension}
            for r in verdict.refusals
        ],
    }
    if verdict.sellable:
        STATS["allowed"] += 1
        out["blocked"] = False
        return out
    if mode == "enforce":
        STATS["blocked"] += 1
        log.warning("MARKET_GATE_BLOCKED family=%s causes=%s", family, ",".join(verdict.causes))
        raise ZidError(
            "MARKET_NOT_RELEASED",
            "This service is not released for this market, so the operation was not run.",
            http_status=403,
            details=[
                ErrorDetail(field=r.dimension or "market", issue=f"{r.cause.value}:{r.code}")
                for r in verdict.refusals
            ],
        )
    STATS["shadow_denied"] += 1
    log.warning("MARKET_GATE_SHADOW_DENIED family=%s causes=%s", family, ",".join(verdict.causes))
    out["blocked"] = False  # reported, not blocking, until the deployment or the company enforces
    return out


async def _payer_country(conn: AsyncConnection, tenant_id: str) -> str | None:
    from sqlalchemy import text

    row = (
        await conn.execute(
            text(
                "SELECT a.country FROM commercial.account a JOIN commercial.account_tenant t "
                "ON t.account_id = a.account_id WHERE t.tenant_id = :t AND t.effective_to IS NULL"
            ),
            {"t": tenant_id},
        )
    ).scalar_one_or_none()
    return str(row) if row else None


async def observe(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    family: str,
    subject_country: str | None,
    operating_region: str | None,
) -> dict[str, Any] | None:
    """Ask the gate about a call that is ALREADY being allowed, and record what it would have said. It never raises and never blocks, whatever the enforcement mode:
    a platform operation is not stopped by this until the owners decide which operations are paid and enforce the gate for them. The payer country is the buyer's own
    commercial account; with none on record it is left unstated, which the gate reports as not evaluated. A failure here is counted and logged, never surfaced."""
    try:
        async with conn.begin_nested():
            decision = await check(
                conn,
                family=family,
                subject_country=subject_country,
                payer_country=await _payer_country(conn, tenant_id),
                operating_region=operating_region,
                enforcement="shadow",
            )
        STATS["observed"] += 1
        return decision
    except Exception:
        STATS["observe_failed"] += 1
        log.exception("MARKET_GATE_OBSERVE_FAILED family=%s", family)
        return None
