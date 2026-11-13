"""Public interface of the assurance module (ZID-UAT-005; CR-074). Other modules import ONLY this.

The rules of independent assurance are pure and live in `domain/rules.py`: the case status machine, manifest completeness, expiry of a PASS when
its pinned inputs change, waivers, fixture review, and the release decision over gates UAT-G0 to UAT-G7. Storage and routes are later slices of CR-074.
"""

from __future__ import annotations

from zid_platform.modules.assurance.domain.rules import (
    Candidate,
    Case,
    Decision,
    RuleError,
    Signature,
    Status,
    evaluate,
    verdict_from,
)

__all__ = [
    "Candidate",
    "Case",
    "Decision",
    "RuleError",
    "Signature",
    "Status",
    "evaluate",
    "verdict_from",
]
