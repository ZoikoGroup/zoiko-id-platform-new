"""Retiring a market's biometric policy (ZID-DTV-WF-04 QA-039; CR-113).

One action, one person (it only stops things, like the kill switch): every biometric method scope for the country that is not already OFF is
stepped down to OFF, and every open proposal for those scopes is rejected, in the caller's transaction. From then on no new session in the country
can use a biometric method (eligibility answers METHOD_OFF). Nothing that was decided before is touched: the method history is append-only, and the
assertions, decisions and evidence of past sessions keep their dispositions and retention. Coming back is a new proposal and a second person.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.application import biometric_methods as svc
from zid_platform.modules.verification.domain import biometric_methods as bm


@dataclass(frozen=True, slots=True)
class Retirement:
    country: str
    retired: tuple[bm.Record, ...]
    withdrawn: tuple[str, ...]  # proposal ids rejected
    already_off: tuple[str, ...]  # scope ids


async def retire_market(
    conn: AsyncConnection, country: str, *, by: str, reason: str, evidence_ref: str, now: datetime
) -> Retirement:
    country = country.upper()
    listing = await svc.list_scopes(conn)
    in_market = [vs for vs in listing["scopes"] if vs and vs[-1].scope.country == country]
    if not in_market:
        raise bm.MethodError("METHOD_NOT_FOUND", f"no biometric method scope for {country}")
    ids = {vs[-1].scope_id for vs in in_market}
    withdrawn = []
    for p in listing["open_proposals"]:
        if p.scope_id in ids:
            await svc.reject(conn, p.proposal_id, by=by, reason=reason, now=now)
            withdrawn.append(p.proposal_id)
    retired, off = [], []
    for vs in sorted(in_market, key=lambda v: v[-1].scope_id):
        cur = vs[-1]
        if cur.state is bm.State.OFF:
            off.append(cur.scope_id)
            continue
        retired.append(
            await svc.step_down(
                conn,
                cur.scope_id,
                target=bm.State.OFF,
                by=by,
                reason=reason,
                evidence_ref=evidence_ref,
                now=now,
            )
        )
    return Retirement(country, tuple(retired), tuple(withdrawn), tuple(off))


__all__ = ["Retirement", "retire_market"]
