"""Regulated claims publisher service (ZID-RCG-003 RCG-G07; CR-077): templates judged against live attestations and market releases.

    judge        every template (latest version), with whether it may be shown now and why not
    publishable  only what may be shown now, for one audience: the text and its proof digest
    proof        the exportable proof bundle for one template at a moment

A certification is judged at the moment asked (its attestation history answers for the past). A market is judged on the releases as they stand
now: a release's launch and withdrawal times are kept, but a past market state is not reconstructed here.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.application import market_releases, suspensions
from zid_platform.modules.verification.domain import regulated_claims as rc
from zid_platform.modules.verification.infrastructure import attestation_store


async def judge(
    conn: AsyncConnection,
    templates: Sequence[rc.ClaimTemplate],
    *,
    at: datetime,
    audience: str | None = None,
) -> list[tuple[rc.ClaimTemplate, rc.ClaimDecision]]:
    current = rc.latest(templates)
    scopes = sorted(
        {s for t in current for s in t.certifications},
        key=lambda s: (s.entity, s.scheme, s.country, s.service_code),
    )
    certifications = (
        await suspensions.hold_suspended(  # a suspended certification backs no claim (CR-078)
            conn, await attestation_store.decide_scopes(conn, scopes, at=at), at=at
        )
    )
    markets = {
        m: await market_releases.market_open(conn, m, now=at)
        for m in sorted(
            {m for t in current for m in t.markets}, key=lambda m: (m.country, m.product, m.purpose)
        )
    }
    return [
        (t, rc.decide(t, at=at, certifications=certifications, markets=markets, audience=audience))
        for t in current
    ]


async def publishable(
    conn: AsyncConnection, templates: Sequence[rc.ClaimTemplate], *, at: datetime, audience: str
) -> list[dict[str, Any]]:
    """What a website, label or sales sheet may show now for `audience`: nothing else."""
    out = []
    for t, d in await judge(conn, templates, at=at, audience=audience):
        if d.publishable:
            bundle = rc.proof_bundle(t, d, at=at)
            out.append(
                {
                    "template_id": t.template_id,
                    "version": t.version,
                    "text": t.text,
                    "countries": sorted(t.countries),
                    "proof_digest": bundle["digest"],
                }
            )
    return out


async def proof(
    conn: AsyncConnection, templates: Sequence[rc.ClaimTemplate], template_id: str, *, at: datetime
) -> dict[str, Any]:
    for t, d in await judge(conn, templates, at=at):
        if t.template_id == template_id:
            return rc.proof_bundle(t, d, at=at)
    raise rc.ClaimTemplateError("CLAIM_TEMPLATE_NOT_FOUND", template_id)
