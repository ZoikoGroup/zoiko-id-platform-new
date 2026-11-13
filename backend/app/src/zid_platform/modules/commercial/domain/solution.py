"""The solution builder's rules (ZID-COM-WF-2026-11 WF-C01, WF-C02). Pure.

Three things live here and none of them prices anything:

- the volumes a buyer expects, validated and normalised (forecast only: an estimate never becomes billed usage);
- the canonical scope of a configuration and its hash, so the same configuration always has the same hash, and any change to it has a different one;
- the offer-card state for a family in a market, with the reasons told apart (legal, provider, country, contract, production), never one vague "unavailable".

An offer card never says AVAILABLE unless every fact in the market rules holds. INCLUDED (already in the buyer's plan) comes from the entitlement view, not
from here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from zid_platform.modules.commercial.domain.market_availability import Cause, Verdict
from zid_platform.modules.commercial.domain.service_families import Family

PURPOSES = (
    "ONBOARDING",
    "COMPLIANCE",
    "PROFESSIONAL_HIRING",
    "REGULATED_TRANSACTION",
    "MARKETPLACE",
)
VOLUME_FIELDS = ("expected_units", "rechecks_per_year", "manual_reviews")
_MAX = 1_000_000_000

CAUSE_TEXT = {
    Cause.CATALOGUE.value: "No signed, effective offer is recorded for this service in this market.",
    Cause.CONTRACT.value: "The Zoiko seller entity or merchant route for this market is not approved.",
    Cause.PROVIDER.value: "Provider or issuer rights for this market are not in force.",
    Cause.LEGAL.value: "The legal basis or the regulated status for this market is not proven.",
    Cause.TAX_PAYMENT.value: "Payment or tax is not configured for this market.",
    Cause.PRODUCTION.value: "The release evidence for this market has not been executed.",
    Cause.COUNTRY.value: "A market was not named, so this service cannot be judged there.",
}


class SolutionError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def normalise_volumes(
    volumes: Mapping[str, Mapping[str, Any]] | None, families: list[str]
) -> dict[str, dict[str, int]]:
    """Expected volumes per selected family. A figure is a non-negative whole number; a family not selected, or a field not known, is refused."""
    out: dict[str, dict[str, int]] = {}
    for family, fields in (volumes or {}).items():
        if family not in families:
            raise SolutionError("VOLUME_FAMILY_NOT_SELECTED", family)
        clean: dict[str, int] = {}
        for name, value in fields.items():
            if name not in VOLUME_FIELDS:
                raise SolutionError("VOLUME_FIELD_UNKNOWN", name)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX:
                raise SolutionError("VOLUME_INVALID", f"{family}.{name}")
            clean[name] = value
        out[family] = dict(sorted(clean.items()))
    return dict(sorted(out.items()))


def scope(
    purpose: str,
    families: list[str],
    subject_country: str | None,
    payer_country: str | None,
    operating_region: str | None,
    volumes: dict[str, dict[str, int]],
) -> dict[str, Any]:
    if purpose not in PURPOSES:
        raise SolutionError("PURPOSE_UNKNOWN", purpose)
    return {
        "purpose": purpose,
        "families": sorted(set(families)),
        "subject_country": subject_country,
        "payer_country": payer_country,
        "operating_region": operating_region,
        "volumes": volumes,
    }


def scope_hash(s: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical JSON of the scope: sorted keys, no whitespace. The same configuration always hashes the same."""
    canonical = json.dumps(s, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def commercial_binding(
    *,
    legal_buyer: str,
    scope_digest: str,
    payer_country: str | None,
    matrix_version: int | None,
    terms: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """What a quote request binds besides the scope (ZID-UX-PRICE-004 P43): the legal buyer, the payer country, the market matrix version, and for every service the market has released the seller entity,
    merchant route, currency, tax class and data region. The hash covers all of it, so a change of seller, route, tax class, currency or matrix version is a different quote. Services the market has not
    released bind nothing here: they are listed as exceptions beside it."""
    body: dict[str, Any] = {
        "legal_buyer": legal_buyer,
        "scope_hash": scope_digest,
        "payer_country": payer_country,
        "market_matrix_version": matrix_version,
        "terms": sorted((dict(t) for t in terms), key=lambda t: str(t.get("family"))),
    }
    return {**body, "binding_hash": scope_hash(body)}


def card_state(
    family: Family, verdict: Verdict, *, market_named: bool, row_recorded: bool
) -> tuple[str, list[dict[str, str]]]:
    """The state of one offer card and why. The states the document names: AVAILABLE, CONTACT_SALES, NOT_ELIGIBLE, RESEARCH_ONLY, plus MARKET_REQUIRED when
    no market was named. NOT_ELIGIBLE is only said when a row exists for the market and a right or the legal basis is the thing missing; with no row at all
    nobody has said no, so the honest next step is to ask."""
    del family  # the state depends on the verdict; the parameter keeps the call site explicit about which family a card is for
    reasons = [{"cause": c, "text": CAUSE_TEXT[c]} for c in verdict.causes]
    if not market_named:
        return "MARKET_REQUIRED", [
            {"cause": Cause.COUNTRY.value, "text": CAUSE_TEXT[Cause.COUNTRY.value]}
        ]
    if verdict.sellable:
        return "AVAILABLE", []
    causes = set(verdict.causes)
    if causes == {Cause.PRODUCTION.value}:
        return "RESEARCH_ONLY", reasons
    if row_recorded and causes & {Cause.PROVIDER.value, Cause.LEGAL.value}:
        return "NOT_ELIGIBLE", reasons
    return "CONTACT_SALES", reasons
