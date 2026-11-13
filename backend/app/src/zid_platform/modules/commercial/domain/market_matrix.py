"""The market matrix rules (ZID-COM-WF-2026-11 v3.0 WF-I01, WF-I05, section 05.1). Pure.

A matrix says, for each (family, country), who sells it, through which merchant route, in which currency, under which tax class, in which data region, and
on which signed evidence. This module decides two things and nothing else:

- whether a row is COMPLETE for its family (a regulated family needs a regulated-status reference, a family that needs a provider needs provider rights, and
  no field is blank), so an incomplete row is refused at the door instead of quietly making a family look sellable;
- which `market_availability.Facts` a recorded row supports, for each dimension a buyer is judged in.

A reference is not proof, and nothing here can make it one. The control is the four independent approvals before activation (each of a different person, none
the author), and the immutability of an activated matrix, both enforced by the database. A row's `payment_configured` and `tax_configured` are recorded
statements that a person signed for, not something this code can verify.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from zid_platform.modules.commercial.domain.market_availability import Facts
from zid_platform.modules.commercial.domain.service_families import FamilyRegistry

ROLES = ("PRODUCT", "FINANCE", "LEGAL_PRIVACY", "SECURITY_QA")
_COUNTRY = re.compile(r"^[A-Z]{2}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class MatrixError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Row:
    family: str
    country: str
    seller_entity: str
    merchant_route: str
    currency: str
    tax_class: str
    data_region: str
    offer_ref: str
    legal_basis_ref: str
    release_evidence_ref: str
    payment_configured: bool
    tax_configured: bool
    provider_rights_ref: str | None = None
    regulated_status_ref: str | None = None
    provider_rights_expires: date | None = None
    regulated_status_expires: date | None = None


def _filled(value: str | None) -> bool:
    return bool(value and value.strip())


def row_problems(row: Row, registry: FamilyRegistry) -> list[str]:
    """Why this row cannot be recorded. Every problem is named, not only the first."""
    out: list[str] = []
    fam = registry.get(row.family)
    if fam is None:
        return ["FAMILY_UNKNOWN"]
    if not _COUNTRY.match(row.country):
        out.append("COUNTRY_INVALID")
    if not _CURRENCY.match(row.currency):
        out.append("CURRENCY_INVALID")
    for name in (
        "seller_entity", "merchant_route", "tax_class", "data_region", "offer_ref",
        "legal_basis_ref", "release_evidence_ref",
    ):  # fmt: skip
        if not _filled(getattr(row, name)):
            out.append(f"{name.upper()}_MISSING")
    if fam.needs_provider and not _filled(row.provider_rights_ref):
        out.append("PROVIDER_RIGHTS_REF_MISSING")
    if _filled(row.provider_rights_ref) and row.provider_rights_expires is None:
        out.append("PROVIDER_RIGHTS_EXPIRY_MISSING")
    if fam.regulated and not _filled(row.regulated_status_ref):
        out.append("REGULATED_STATUS_REF_MISSING")
    if _filled(row.regulated_status_ref) and row.regulated_status_expires is None:
        out.append("REGULATED_STATUS_EXPIRY_MISSING")
    return out


def check_row(row: Row, registry: FamilyRegistry) -> None:
    problems = row_problems(row, registry)
    if problems:
        raise MatrixError(problems[0], ",".join(problems))


def _in_force(ref: str | None, expires: date | None, on: date) -> bool:
    """A reference supports a sale only while its expiry date has not passed. An expiry that was never recorded supports nothing."""
    return _filled(ref) and expires is not None and expires >= on


def facts_for_row(row: Row | None, family_code: str, registry: FamilyRegistry, on: date) -> Facts:
    """What an ACTIVE matrix's row supports for one family. No row at all supports nothing: every fact stays unrecorded, which is a refusal."""
    if row is None or registry.get(family_code) is None:
        return Facts()
    fam = registry.families[family_code]
    return Facts(
        offer_effective=_filled(row.offer_ref),
        seller_entity_approved=_filled(row.seller_entity),
        merchant_route_supported=_filled(row.merchant_route),
        provider_entitled=_in_force(row.provider_rights_ref, row.provider_rights_expires, on)
        if fam.needs_provider
        else True,
        legal_basis_documented=_filled(row.legal_basis_ref),
        regulated_status_proven=_in_force(
            row.regulated_status_ref, row.regulated_status_expires, on
        )
        if fam.regulated
        else True,
        payment_configured=row.payment_configured,
        tax_configured=row.tax_configured,
        release_evidence_executed=_filled(row.release_evidence_ref),
    )


def facts_by_dimension(
    rows: Mapping[tuple[str, str], Row],
    registry: FamilyRegistry,
    family_code: str,
    *,
    subject_country: str | None,
    payer_country: str | None,
    operating_region: str | None,
    on: date,
) -> dict[str, Facts]:
    """The three dimensions a buyer is judged in, from an active matrix. A dimension the caller did not name is left out, so it is refused as
    'not evaluated', never passed. The operating region is judged against the data region the subject's row is recorded for."""
    out: dict[str, Facts] = {}
    if subject_country:
        out["subject"] = facts_for_row(
            rows.get((family_code, subject_country)), family_code, registry, on
        )
    if payer_country:
        out["payer"] = facts_for_row(
            rows.get((family_code, payer_country)), family_code, registry, on
        )
    if operating_region:
        regional = [
            r
            for (fam, _c), r in rows.items()
            if fam == family_code and r.data_region == operating_region
        ]
        out["operating"] = facts_for_row(
            regional[0] if regional else None, family_code, registry, on
        )
    return out


def missing_approvals(signed_roles: Sequence[str]) -> tuple[str, ...]:
    """The roles that still have to sign before activation."""
    return tuple(r for r in ROLES if r not in set(signed_roles))


def rights_alerts(rows: Sequence[Row], on: date, within_days: int = 30) -> list[dict[str, str]]:
    """Rights that have expired or expire within `within_days`, for the operator to renew before they stop a sale (WF-I01 'rights expiring')."""
    horizon = on + timedelta(days=within_days)
    out: list[dict[str, str]] = []
    for r in rows:
        for kind, ref, expires in (
            ("PROVIDER_RIGHTS", r.provider_rights_ref, r.provider_rights_expires),
            ("REGULATED_STATUS", r.regulated_status_ref, r.regulated_status_expires),
        ):
            if not _filled(ref) or expires is None:
                continue
            if expires < on:
                out.append(
                    {
                        "family": r.family,
                        "country": r.country,
                        "kind": kind,
                        "state": "EXPIRED",
                        "expires": expires.isoformat(),
                    }
                )
            elif expires <= horizon:
                out.append(
                    {
                        "family": r.family,
                        "country": r.country,
                        "kind": kind,
                        "state": "EXPIRING",
                        "expires": expires.isoformat(),
                    }
                )
    return out
