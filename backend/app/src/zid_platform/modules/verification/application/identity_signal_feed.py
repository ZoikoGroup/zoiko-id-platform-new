"""Identity signals into the reviewer queue (CR-101; ZID-GAP-001 v2.0 DTV-20, DTV-21, DTV-22).

The rules in `domain/identity_signals.py` find groups of people connected by shared phones, emails and devices. This module turns what they
find into review items in the screening queue that already exists (`/v1/reviews/signals`), so a person looks at it there. It carries opaque
subject references, rule codes and counts: never a phone, an email or a device id. A finding is a reason to look and nothing else; it blocks,
approves and clears nobody.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.screening.ports import IdentityFinding, RiskTier, raise_identity_findings
from zid_platform.modules.verification.domain import identity_signals as sig

_TIER = {"WEAK": RiskTier.ELEVATED, "MODERATE": RiskTier.ELEVATED, "STRONG": RiskTier.HIGH}


def _key(subjects: Iterable[str]) -> str:
    return hashlib.sha256("\x1f".join(sorted(subjects)).encode()).hexdigest()[:32]


def findings_from(observations: Iterable[sig.Observation]) -> list[IdentityFinding]:
    """Deterministic: the same observations give the same findings in the same order."""
    obs = list(observations)
    out: list[IdentityFinding] = []
    for link in sig.link_graph(obs):
        if link.strength == "WEAK":
            continue  # one kind of identifier shared is ordinary (a household, an office): worth counting, not worth a person's time
        counts = {
            "people": len(link.subjects),
            "kinds_shared": len(link.shared),
            "identifiers_shared": sum(n for _, n in link.shared),
        }
        out.append(
            IdentityFinding(
                rule=f"IDENTITY_LINK_{link.strength}",
                subject_ref=link.subjects[0],
                group_key=_key(link.subjects),
                tier=_TIER[link.strength],
                counts=counts,
            )
        )
    for dev in sig.shared_devices(obs):
        out.append(
            IdentityFinding(
                rule=dev.code,
                subject_ref=dev.subjects[0],
                group_key=_key([dev.device, *dev.subjects]),
                tier=RiskTier.ELEVATED,
                counts={"people": len(dev.subjects)},
            )
        )
    return out


async def raise_for(
    conn: AsyncConnection, tenant_id: str, observations: Iterable[sig.Observation], *, now: datetime
) -> int:
    """Open a review item for each finding. Returns how many were NEW (a group already open adds nothing)."""
    raised = await raise_identity_findings(conn, tenant_id, findings_from(observations), now=now)
    return sum(1 for r in raised if r.created)


# ---- observations: what the rules are fed from -------------------------------------------------------------------------------------------------

RETAIN = timedelta(days=90)  # PROPOSED: Privacy owns the number
WINDOW_LIMIT = 5000  # the newest rows a company's link graph is built from in one pass


async def observe(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    subject: str,
    kind: sig.Kind,
    value: str,
    pepper: bytes,
    now: datetime,
) -> None:
    """Remember that this person was seen with this identifier, as a keyed digest only. Best effort: it never raises and never blocks the caller."""
    if not value or not pepper:
        return  # an unkeyed hash of a short value can be reversed by guessing, so without the cell's secret nothing is kept
    try:
        digest = sig.identifier_hash(value, tenant_id=tenant_id, kind=kind, pepper=pepper)
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "INSERT INTO verify.identity_observation (tenant_id, subject, kind, identifier, first_seen, last_seen) "
                    "VALUES (:t, :s, :k, :i, :n, :n) ON CONFLICT (tenant_id, subject, kind, identifier) DO UPDATE SET last_seen = :n"
                ),
                {
                    "t": uuid.UUID(tenant_id),
                    "s": uuid.UUID(subject),
                    "k": kind.value,
                    "i": digest,
                    "n": now,
                },
            )
    except Exception:  # noqa: BLE001 - a record that cannot be written must not stop a verification
        return


async def observe_request(engine: Any, ctx: Any, subject: str, request: Any, now: datetime) -> None:
    """Record the device the request came from (the `x-zid-device` header, hashed with the cell's pepper) against the person being verified. Does
    nothing when the client sent no device id. Only a device is observed here: no phone or email is collected at this point."""
    from zid_db import tenant_transaction

    from zid_platform.attempt_context import attempt_inputs

    given = attempt_inputs(request)
    if not given.get("device") or not ctx.tenant_id:
        return
    async with tenant_transaction(engine, ctx) as conn:
        await observe(
            conn,
            ctx.tenant_id,
            subject=subject,
            kind=sig.Kind.DEVICE,
            value=str(given["device"]),
            pepper=given.get("pepper") or b"",
            now=now,
        )


async def feed_pass(conn: AsyncConnection, now: datetime) -> dict[str, int]:
    """Worker pass (it sees every company): for each company with new observations, run the link rules over the recent ones and open review items;
    then drop what is past retention. A group already open adds nothing."""
    tenants = (
        await conn.execute(
            text(
                "SELECT DISTINCT tenant_id FROM verify.identity_observation WHERE last_seen >= :since"
            ),
            {"since": now - timedelta(days=1)},
        )
    ).all()
    opened = 0
    for (tenant,) in tenants:
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant)})
        rows = (
            await conn.execute(
                text(
                    "SELECT subject, kind, identifier FROM verify.identity_observation WHERE tenant_id = :t "
                    "ORDER BY last_seen DESC LIMIT :n"
                ),
                {"t": tenant, "n": WINDOW_LIMIT},
            )
        ).all()
        observations = [
            sig.Observation(str(r.subject), sig.Kind(r.kind), r.identifier) for r in rows
        ]
        opened += await raise_for(conn, str(tenant), observations, now=now)
    dropped = (
        await conn.execute(
            text("DELETE FROM verify.identity_observation WHERE last_seen < :cut RETURNING 1"),
            {"cut": now - RETAIN},
        )
    ).rowcount
    return {"companies": len(tenants), "opened": opened, "dropped": int(dropped or 0)}
