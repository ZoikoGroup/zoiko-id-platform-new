"""Privacy, residency and subprocessor disclosures (ZID-CP-WF12 v2.0, WF-12.05). Pure domain, no I/O.

A public privacy statement is a record, not copy: each kind has required fields, a notice period comes only from a contract, a retention rule needs a lawful basis and a controller
instruction, and a statement that data stays in a region is refused while any subprocessor holding it sits elsewhere.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

KINDS = ("subprocessor", "retention", "biometric", "transfer")
ACCESS_MODES = ("hosting", "processing", "support_remote", "analytics", "none")
RESTRICTED_CLASSES = (
    "DC2",
    "DC3",
    "KEY_MATERIAL",
)  # residency data classes whose location a regional claim must cover
_DAYS = re.compile(r"\b\d+\s*(?:-|\s)?\s*(?:day|days|month|months|year|years)\b", re.IGNORECASE)
_COUNTRY = re.compile(r"^[A-Z]{2}$")


def _need(p: Mapping[str, Any], *fields: str) -> list[str]:
    out = []
    for f in fields:
        v = p.get(f)
        if (
            v is None
            or (isinstance(v, str) and not v.strip())
            or (isinstance(v, (list, tuple)) and not v)
        ):
            out.append(f"{f} is required.")
    return out


def problems(kind: str, subject: str, payload: Mapping[str, Any]) -> list[str]:
    """Everything wrong with one disclosure's content. Empty means it may be put forward for approval."""
    if kind not in KINDS:
        return [f"Unknown kind {kind}."]
    out: list[str] = []
    if kind == "subprocessor":
        out += _need(
            payload, "purpose", "country", "access_mode", "data_categories", "contract_ref"
        )
        if payload.get("country") and not _COUNTRY.match(str(payload["country"])):
            out.append("country is a two-letter code.")
        if payload.get("access_mode") not in ACCESS_MODES:
            out.append(f"access_mode is one of {', '.join(ACCESS_MODES)}.")
        n = payload.get("notice_days")
        if n is not None and (not isinstance(n, int) or not 0 <= n <= 365):
            out.append("notice_days is a whole number of days taken from the contract.")
        if n is not None and not payload.get("notice_source"):
            out.append(
                "notice_days must name the contract clause or law it comes from (notice_source); a notice period is never universal."
            )
    elif kind == "retention":
        out += _need(payload, "data_category", "rule", "legal_basis", "instruction_ref")
        if not payload.get("legal_basis") and _DAYS.search(str(payload.get("rule", ""))):
            out.append("A period of time needs its lawful basis.")
    elif kind == "biometric":
        out += _need(payload, "necessity", "safeguards", "alternative_route", "capability_ref")
        if payload.get("dpia_applicable") is True and not payload.get("dpia_ref"):
            out.append("A DPIA that applies needs its reference.")
    elif kind == "transfer":
        out += _need(payload, "from_country", "to_country", "mechanism")
        for f in ("from_country", "to_country"):
            if payload.get(f) and not _COUNTRY.match(str(payload[f])):
                out.append(f"{f} is a two-letter code.")
        for f in ("remote_support", "backups"):
            if f not in payload or not isinstance(payload[f], bool):
                out.append(f"{f} must say yes or no: remote support and backups are transfers too.")
    return out


def text_problems(text: str) -> list[str]:
    """Public prose in any disclosure: no universal notice promise, no hard-coded retention number without a source."""
    out = []
    if re.search(r"\b30[- ]?day", text, re.IGNORECASE) and "contract" not in text.lower():
        out.append(
            "A 30-day notice is not universal; state the period from the contract it comes from."
        )
    return out


def region_claim_blockers(
    region: str, subprocessors: Sequence[Mapping[str, Any]], transfers: Sequence[Mapping[str, Any]]
) -> list[str]:
    """May the company say its restricted data stays in this region (a two-letter country, or a bloc listed by the caller)? Not while any active subprocessor that touches those data classes
    is elsewhere, nor while a transfer or remote support route leaves it."""
    why: list[str] = []
    for s in subprocessors:
        classes = set(s.get("data_categories", []))
        if (
            classes & set(RESTRICTED_CLASSES)
            and s.get("country") != region
            and s.get("access_mode") != "none"
        ):
            why.append(
                f"{s.get('name', 'A subprocessor')} handles {', '.join(sorted(classes & set(RESTRICTED_CLASSES)))} from {s.get('country')}."
            )
    for t in transfers:
        if t.get("from_country") == region and t.get("to_country") != region:
            why.append(f"A transfer to {t.get('to_country')} is on record ({t.get('mechanism')}).")
        if (
            t.get("from_country") == region
            and (t.get("remote_support") or t.get("backups"))
            and t.get("to_country") != region
        ):
            why.append("Remote support or backups leave the region.")
    return why
