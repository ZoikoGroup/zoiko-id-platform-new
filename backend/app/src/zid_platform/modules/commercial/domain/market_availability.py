"""Is this service family sellable in this market? (ZID-COM-WF-2026-11 v3.0, "Coverage truth", section 05.1, WF-C01, WF-I05, the SAFETY STOP.) Pure.

The document's rule: a service is sellable only with an effective offer, an approved seller entity, an entitled provider or issuer, a market-specific legal
basis, a supported payment and tax configuration, and executed release evidence. "Designed" or "tested locally" is not "globally sellable". So:

- every fact is tri-state: True (shown), False (shown not to hold) or None (nobody recorded it). None is a refusal, never a pass: with no recorded fact the
  answer is REQUEST A QUOTE, which is what the pricing page must say instead of inventing a price (CAT-06);
- the subject's country, the payer's country and the operating region are judged SEPARATELY and the sale needs all of them (CAT-03, CAT-04): an allowed
  domestic issuer does not make a restricted foreign payer sellable, and a country switch in the browser changes nothing here;
- a refusal names its cause so the screen can say whether it is legal, provider, country, contract, production, or tax and payment (WF-C01: "Not available
  must distinguish legal/provider/country/contract/production reasons");
- a regulated family also needs its regulatory status proven for the market (INV-11); an unsupported regulated status (a qualified signature) can never be sold.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from zid_platform.modules.commercial.domain.service_families import Family

DIMENSIONS = ("subject", "payer", "operating")


class Cause(StrEnum):
    CATALOGUE = "CATALOGUE"  # no signed, effective offer
    CONTRACT = "CONTRACT"  # seller entity, merchant route
    PROVIDER = "PROVIDER"  # provider or issuer rights
    LEGAL = "LEGAL"  # legal basis, regulated status
    TAX_PAYMENT = "TAX_PAYMENT"
    PRODUCTION = "PRODUCTION"  # release evidence
    COUNTRY = "COUNTRY"  # a dimension nobody evaluated


@dataclass(frozen=True, slots=True)
class Facts:
    """What is recorded for one family in one market dimension. None means nobody recorded it, which is never a pass."""

    offer_effective: bool | None = None
    seller_entity_approved: bool | None = None
    merchant_route_supported: bool | None = None
    provider_entitled: bool | None = None
    legal_basis_documented: bool | None = None
    regulated_status_proven: bool | None = None
    payment_configured: bool | None = None
    tax_configured: bool | None = None
    release_evidence_executed: bool | None = None


@dataclass(frozen=True, slots=True)
class Refusal:
    code: str
    cause: Cause
    dimension: str | None = None


@dataclass(frozen=True, slots=True)
class Verdict:
    family: str
    sellable: bool
    refusals: tuple[Refusal, ...]

    @property
    def next_action(self) -> str:
        return "SELECT_PLAN" if self.sellable else "REQUEST_QUOTE"

    @property
    def causes(self) -> tuple[str, ...]:
        return tuple(sorted({r.cause.value for r in self.refusals}))


def _need(
    value: bool | None, name: str, cause: Cause, out: list[Refusal], dimension: str | None
) -> None:
    if value is None:
        out.append(Refusal(f"{name}_UNKNOWN", cause, dimension))
    elif value is False:
        out.append(Refusal(f"{name}_NOT_SATISFIED", cause, dimension))


def refusals_for(family: Family, facts: Facts, dimension: str | None = None) -> list[Refusal]:
    """Every reason this family is not sellable in one dimension. All are reported, not the first."""
    out: list[Refusal] = []
    _need(facts.offer_effective, "OFFER_EFFECTIVE", Cause.CATALOGUE, out, dimension)
    _need(facts.seller_entity_approved, "SELLER_ENTITY", Cause.CONTRACT, out, dimension)
    _need(facts.merchant_route_supported, "MERCHANT_ROUTE", Cause.CONTRACT, out, dimension)
    if family.needs_provider:
        _need(facts.provider_entitled, "PROVIDER_RIGHTS", Cause.PROVIDER, out, dimension)
    _need(facts.legal_basis_documented, "LEGAL_BASIS", Cause.LEGAL, out, dimension)
    if family.regulated:
        _need(facts.regulated_status_proven, "REGULATED_STATUS", Cause.LEGAL, out, dimension)
    _need(facts.payment_configured, "PAYMENT", Cause.TAX_PAYMENT, out, dimension)
    _need(facts.tax_configured, "TAX", Cause.TAX_PAYMENT, out, dimension)
    _need(facts.release_evidence_executed, "RELEASE_EVIDENCE", Cause.PRODUCTION, out, dimension)
    return out


def decide(family: Family, by_dimension: Mapping[str, Facts]) -> Verdict:
    """Sellable only when EVERY dimension (subject country, payer country, operating region) is satisfied. A dimension nobody evaluated is a refusal."""
    refusals: list[Refusal] = []
    for dim in DIMENSIONS:
        facts = by_dimension.get(dim)
        if facts is None:
            refusals.append(Refusal(f"{dim.upper()}_NOT_EVALUATED", Cause.COUNTRY, dim))
            continue
        refusals.extend(refusals_for(family, facts, dim))
    # The document issuer's country is judged only when the buyer names one and the family depends on it. Naming none is not a refusal; naming one the row does not list is.
    issuer = by_dimension.get("issuer")
    if issuer is not None and family.issuer_scoped:
        _need(issuer.provider_entitled, "ISSUER_COVERAGE", Cause.PROVIDER, refusals, "issuer")
    return Verdict(family.code, not refusals, tuple(refusals))
