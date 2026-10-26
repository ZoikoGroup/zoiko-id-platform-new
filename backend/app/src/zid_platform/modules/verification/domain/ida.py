"""OpenID Connect for Identity Assurance projection (ZID-VERIFY-001 VERIFY-25). Pure domain.

An internal verification assertion becomes a `verified_claims` entry for a relying party, and nothing more than it
asked for and may have: only VALID, unexpired assertions; only claim types the party requested; only when the
assertion's profile lists the party's purpose for reliance. Zoiko holds commitments, not values, so each claim is
attested (`true`) rather than disclosed, and the entry carries the assertion id so every statement maps back to a
recorded internal assertion. Evidence is reported by type only, never by provider or document.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from datetime import datetime
from typing import Any

TRUST_FRAMEWORK = "zoiko_ial"
IAL_RANK = {"IAL-Z0": 0, "IAL-Z1": 1, "IAL-Z2": 2, "IAL-Z3": 3}


def project(
    assertions: Sequence[Mapping[str, Any]],
    *,
    purpose: str,
    requested: Collection[str],
    now: datetime,
    min_level: str = "IAL-Z1",
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for a in assertions:
        if a["claim_type"] not in requested or a["status"] != "VALID":
            continue
        if a.get("valid_until") is not None and a["valid_until"] <= now:
            continue
        if purpose not in (a.get("purposes") or []):
            continue
        if IAL_RANK.get(a["assurance_level"], 0) < IAL_RANK[min_level]:
            continue
        out.append(
            {
                "verification": {
                    "trust_framework": TRUST_FRAMEWORK,
                    "assurance_level": a["assurance_level"],
                    "time": a["performed_at"].isoformat(),
                    "verification_process": str(a["assertion_id"]),
                    "evidence": [{"type": t} for t in a.get("evidence_types") or []],
                },
                "claims": {a["claim_type"]: True},
            }
        )
    return out
