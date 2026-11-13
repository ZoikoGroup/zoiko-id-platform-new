"""The public pricing page's structure (ZID-UX-PRICE-004 v4.0). Pure.

Parses and validates `pricing-page.yaml`. A file that does not validate never loads. Every family must have a state in every tier, every use case must name real
families, and the wording must carry no price, discount, trial or status claim. The structure holds no price and no availability: those come from the approved price book
and the active market matrix at request time.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from zid_platform.modules.commercial.domain.service_families import EXPECTED_CODES

# What a buyer sees for each judgement the market matrix gives (WF-P06). Reasons are told apart, never one vague "unavailable".
PUBLIC_STATE = {
    "AVAILABLE": "AVAILABLE",
    "CONTACT_SALES": "SALES_ASSISTED",
    "NOT_ELIGIBLE": "RESTRICTED",
    "RESEARCH_ONLY": "NOT_AVAILABLE",
    "MARKET_REQUIRED": "MARKET_REQUIRED",
}
TIER_CODES = ("ESSENTIALS", "BUSINESS", "ENTERPRISE")
FAMILY_STATES = ("included", "optional", "quote")
STATUSES = ("PROPOSED", "APPROVED")

_MONEY = re.compile(
    r"[$€£¥]|\b(usd|eur|gbp|jpy)\b|\b\d+(\.\d+)?\s?%|\bper\s+(month|year|check|verification)\b",
    re.IGNORECASE,
)
_CLAIM = re.compile(
    r"\b(free|trial|certified|qualified|accredited|licen[sc]ed|globally|worldwide|guaranteed|unlimited)\b",
    re.IGNORECASE,
)


class PageError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Tier:
    code: str
    label: str
    fit: str
    cta_kind: str
    cta_label: str
    family_state: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class PathBand:
    code: str
    label: str
    text: str
    cta_kind: str
    cta_label: str


@dataclass(frozen=True, slots=True)
class UseCase:
    code: str
    label: str
    families: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PricingPage:
    version: str
    status: str
    tiers: tuple[Tier, ...]
    paths: tuple[PathBand, ...]
    use_cases: tuple[UseCase, ...]
    disclosures: Mapping[str, str]


def _clean(value: Any, where: str, *, label: bool = True) -> str:
    s = str(value or "").strip()
    if not s:
        raise PageError("TEXT_MISSING", where)
    if _MONEY.search(s):
        raise PageError("PRICE_IN_TEXT", f"{where}: {s}")
    if label and _CLAIM.search(s):
        raise PageError("CLAIM_IN_TEXT", f"{where}: {s}")
    return s


def parse(doc: Mapping[str, Any]) -> PricingPage:
    status = str(doc.get("status", ""))
    if status not in STATUSES:
        raise PageError("STATUS_INVALID", status)
    tiers: list[Tier] = []
    for t in doc.get("tiers") or []:
        code = str(t.get("code", ""))
        states = t.get("family_state") or {}
        if set(states) != EXPECTED_CODES:
            raise PageError(
                "TIER_FAMILIES_INCOMPLETE", f"{code}: {sorted(EXPECTED_CODES ^ set(states))}"
            )
        for fam, state in states.items():
            if state not in FAMILY_STATES:
                raise PageError("FAMILY_STATE_INVALID", f"{code}.{fam}={state}")
        tiers.append(
            Tier(code, _clean(t.get("label"), f"{code}.label"), _clean(t.get("fit"), f"{code}.fit"),
                 str(t.get("cta_kind", "")), _clean(t.get("cta_label"), f"{code}.cta_label"), dict(states))
        )  # fmt: skip
    if tuple(t.code for t in tiers) != TIER_CODES:
        raise PageError("TIERS_CHANGED", str([t.code for t in tiers]))
    paths = tuple(
        PathBand(
            str(p["code"]),
            _clean(p["label"], "path.label"),
            _clean(p["text"], "path.text", label=False),
            str(p["cta_kind"]),
            _clean(p["cta_label"], "path.cta_label"),
        )
        for p in doc.get("paths") or []
    )
    if {p.code for p in paths} != {"SANDBOX", "PARTNER"}:
        raise PageError("PATHS_CHANGED", str([p.code for p in paths]))
    cases: list[UseCase] = []
    for u in doc.get("use_cases") or []:
        fams = tuple(str(f) for f in u.get("families") or [])
        if not fams or any(f not in EXPECTED_CODES for f in fams):
            raise PageError("USE_CASE_FAMILY_UNKNOWN", str(u.get("code")))
        cases.append(UseCase(str(u["code"]), _clean(u["label"], f"use_case.{u['code']}"), fams))
    if len({c.code for c in cases}) != len(cases) or not cases:
        raise PageError("USE_CASES_INVALID")
    disclosures = {
        k: _clean(v, f"disclosures.{k}", label=False)
        for k, v in (doc.get("disclosures") or {}).items()
    }
    for needed in ("price_basis", "tax", "commitment", "no_surprise", "plan_change", "exit"):
        if needed not in disclosures:
            raise PageError("DISCLOSURE_MISSING", needed)
    return PricingPage(
        str(doc.get("version", "")), status, tuple(tiers), paths, tuple(cases), disclosures
    )
