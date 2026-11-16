"""Identity signals into the reviewer queue (CR-101; ZID-GAP-001 v2.0 DTV-20, DTV-21, DTV-22).

The rules in `domain/identity_signals.py` find groups of people connected by shared phones, emails and devices. This module turns what they
find into review items in the screening queue that already exists (`/v1/reviews/signals`), so a person looks at it there. It carries opaque
subject references, rule codes and counts: never a phone, an email or a device id. A finding is a reason to look and nothing else; it blocks,
approves and clears nobody.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import datetime

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
