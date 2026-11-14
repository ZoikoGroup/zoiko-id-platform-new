"""Wording revisions for the service families (ZID-COM-WF-2026-11 WF-I01, G01, INV-11). Pure.

Only what a buyer reads can be revised: the name, the unit and the sales qualifier. The structure that decides behaviour (codes, dependencies, regulated, provider, meter)
is not here and cannot be reached from here: `apply_text` replaces three fields and nothing else. New wording is also checked so it cannot become a price or a claim the
documents forbid: no money, no "free", no per-unit rate, and, in the name or unit, none of the words "qualified", "certified", "accredited", "licensed", "globally",
"worldwide" or "guaranteed" (a regulated or global status is proven in the market matrix, never asserted in a label).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace

from zid_platform.modules.commercial.domain.service_families import FamilyRegistry

_PRICE = re.compile(
    r"[$€£¥]|\b(usd|eur|gbp|jpy|dollars?|euros?|pounds?)\b|\bfree\b|\bper\s+(month|year|day|check|verification|call|user|seat)\b|\b\d+(\.\d+)?\s?%",
    re.IGNORECASE,
)
_CLAIM = re.compile(
    r"\b(qualified|certified|accredited|licen[sc]ed|globally|worldwide|guaranteed)\b", re.IGNORECASE
)


class TextError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Text:
    name: str
    unit: str
    sales_qualifier: str


def check_text(name: str, unit: str, sales_qualifier: str) -> Text:
    """Validate and normalise new wording, or raise naming the first problem."""
    n, u, q = name.strip(), unit.strip(), sales_qualifier.strip()
    if not 2 <= len(n) <= 80:
        raise TextError("NAME_LENGTH", "2 to 80 characters")
    if not 2 <= len(u) <= 120:
        raise TextError("UNIT_LENGTH", "2 to 120 characters")
    if len(q) > 300:
        raise TextError("QUALIFIER_LENGTH", "at most 300 characters")
    for label, value in (("name", n), ("unit", u), ("sales_qualifier", q)):
        if _PRICE.search(value):
            raise TextError(
                "PRICE_IN_TEXT", f"{label} must not state a price, rate, discount or free offer"
            )
    for label, value in (("name", n), ("unit", u)):
        if _CLAIM.search(value):
            raise TextError(
                "CLAIM_IN_LABEL",
                f"{label} must not claim a status that is proven per market, not in a label",
            )
    return Text(n, u, q)


def apply_text(registry: FamilyRegistry, texts: Mapping[str, Text]) -> FamilyRegistry:
    """The registry with the active wording laid over the file's. Only name, unit and sales qualifier change; a revision for an unknown family is ignored."""
    families = {
        code: replace(fam, name=t.name, unit=t.unit, sales_qualifier=t.sales_qualifier)
        if (t := texts.get(code))
        else fam
        for code, fam in registry.families.items()
    }
    return replace(registry, families=families)
