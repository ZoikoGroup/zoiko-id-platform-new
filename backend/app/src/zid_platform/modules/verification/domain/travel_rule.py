"""Crypto Travel Rule: IVMS101 payload validation and the counterparty VASP check (ZID-GAP-001 v2.0 DTV-33; CR-073 section D; CR-097). Pure domain.

The Travel Rule needs a protocol network (a licensed partner) to move the message between two virtual asset service providers. That part is not here and
cannot be: it needs a partner contract and a legal basis, and the product line ships as DESIGN. What is here is the part that is ours and checkable
without a partner: is the payload well formed, is the counterparty a provider we can name and have reason to trust, and what is missing.

- the payload is the IVMS101 data model (originator, beneficiary, the two VASPs, the transfer). A field is checked for shape, never for truth: a
  well-formed name is not a verified person, and this module says so in its result;
- a counterparty VASP counts as KNOWN only when its LEI is valid (ISO 17442 check digits) AND it is listed in the registry the operators configured AND
  that listing is ACTIVE for the jurisdiction of the transfer. An unlisted provider is UNVERIFIED_COUNTERPARTY, never assumed fine because it looks
  plausible. The shipped registry is empty, so every counterparty is unverified until an operator records real ones;
- whether the Travel Rule applies at all depends on a threshold that differs by jurisdiction and is a legal value. None ships. With no threshold for a
  jurisdiction the answer is THRESHOLD_UNKNOWN, never "below the threshold";
- nothing here sends anything, stores anything, or decides to release or hold a transfer. It returns findings for a compliance person.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

LINE = "travel_rule"
NAME_TYPES = frozenset(
    {"ALIA", "BIRT", "MAID", "LEGL", "MISC"}
)  # IVMS101 NaturalPersonNameTypeCode and LegalPersonNameTypeCode
ADDRESS_TYPES = frozenset({"HOME", "BIZZ", "GEOG"})
NATIONAL_ID_TYPES = frozenset(
    {"ARNU", "CCPT", "RAID", "DRLC", "FIIN", "TXID", "SOCS", "IDCD", "LEIX", "MISC"}
)
COUNTRY = re.compile(r"^[A-Z]{2}$")
CURRENCY = re.compile(r"^[A-Z]{3,10}$")  # fiat codes and asset tickers
MAX_PERSONS = 10
MAX_TEXT = 140


class TravelRuleError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class Counterparty(StrEnum):
    KNOWN = "KNOWN"
    UNVERIFIED_COUNTERPARTY = "UNVERIFIED_COUNTERPARTY"
    NOT_ACTIVE_FOR_JURISDICTION = "NOT_ACTIVE_FOR_JURISDICTION"
    LEI_INVALID = "LEI_INVALID"
    NOT_PROVIDED = "NOT_PROVIDED"


class Threshold(StrEnum):
    BELOW = "BELOW_THRESHOLD"
    AT_OR_ABOVE = "AT_OR_ABOVE_THRESHOLD"
    UNKNOWN = "THRESHOLD_UNKNOWN"


@dataclass(frozen=True, slots=True)
class RegisteredVasp:
    lei: str
    legal_name: str
    jurisdictions: frozenset[str]
    status: str  # ACTIVE | SUSPENDED | WITHDRAWN
    source_ref: str  # where the operator learned it: a register entry, a contract, a regulator list


@dataclass(frozen=True, slots=True)
class VaspRegistry:
    version: str
    entries: Mapping[str, RegisteredVasp]
    thresholds: Mapping[str, tuple[Decimal, str]]  # jurisdiction -> (amount, currency)


def lei_ok(lei: str) -> bool:
    """ISO 17442: 20 characters, the last two check digits, mod 97 equals 1."""
    if not re.fullmatch(r"[A-Z0-9]{18}[0-9]{2}", lei or ""):
        return False
    return int("".join(str(int(c, 36)) for c in lei)) % 97 == 1


def parse_registry(doc: Mapping[str, Any]) -> VaspRegistry:
    """The operators' registry. Malformed entries refuse the whole file: a half-read registry would quietly trust fewer or more providers than meant."""
    entries: dict[str, RegisteredVasp] = {}
    for item in doc.get("vasps") or []:
        lei = str(item.get("lei", "")).upper()
        if not lei_ok(lei) or lei in entries:
            raise TravelRuleError("REGISTRY_INVALID", f"lei {lei!r} is malformed or repeated")
        status = str(item.get("status", ""))
        if status not in {"ACTIVE", "SUSPENDED", "WITHDRAWN"}:
            raise TravelRuleError("REGISTRY_INVALID", f"{lei}: status")
        countries = frozenset(str(c).upper() for c in item.get("jurisdictions") or ())
        if (
            not countries
            or any(not COUNTRY.match(c) for c in countries)
            or not item.get("source_ref")
        ):
            raise TravelRuleError(
                "REGISTRY_INVALID", f"{lei}: jurisdictions and source_ref are required"
            )
        entries[lei] = RegisteredVasp(
            lei, str(item.get("legal_name", "")), countries, status, str(item["source_ref"])
        )
    thresholds: dict[str, tuple[Decimal, str]] = {}
    for country, spec in (doc.get("thresholds") or {}).items():
        try:
            amount = Decimal(str(spec["amount"]))
        except (InvalidOperation, KeyError, TypeError) as e:
            raise TravelRuleError("REGISTRY_INVALID", f"threshold for {country}") from e
        if (
            not COUNTRY.match(str(country).upper())
            or not amount.is_finite()
            or amount < 0
            or not spec.get("legal_basis_ref")
        ):
            raise TravelRuleError(
                "REGISTRY_INVALID",
                f"threshold for {country} needs a country, an amount and a legal basis reference",
            )
        thresholds[str(country).upper()] = (amount, str(spec.get("currency", "")).upper())
    return VaspRegistry(str(doc.get("version", "")), entries, thresholds)


# ---------------------------------------------------------------------------------------------------------------------- payload


@dataclass(frozen=True, slots=True)
class Finding:
    code: str  # e.g. ORIGINATOR_NAME_MISSING
    path: str  # where in the payload, never the value


@dataclass(frozen=True, slots=True)
class Assessment:
    well_formed: bool
    findings: tuple[Finding, ...]
    originating_vasp: Counterparty
    beneficiary_vasp: Counterparty
    threshold: Threshold
    notes: tuple[str, ...] = field(default=())
    originating_lei: str | None = (
        None  # a company identifier as stated in the payload, never a person
    )
    beneficiary_lei: str | None = None


def _persons(block: Any, key: str, path: str, out: list[Finding]) -> list[Mapping[str, Any]]:
    if not isinstance(block, Mapping):
        out.append(Finding("BLOCK_MISSING", path))
        return []
    people = block.get(key)
    if (
        not isinstance(people, Sequence)
        or isinstance(people, str | bytes)
        or not 1 <= len(people) <= MAX_PERSONS
    ):
        out.append(Finding("PERSON_LIST_INVALID", f"{path}.{key}"))
        return []
    return [p for p in people if isinstance(p, Mapping)]


def _text(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= MAX_TEXT


def _name_identifiers(ids: Any, path: str, out: list[Finding], *, natural: bool) -> None:
    if not isinstance(ids, Sequence) or isinstance(ids, str | bytes) or not ids:
        out.append(Finding("NAME_MISSING", path))
        return
    for i, ident in enumerate(ids):
        where = f"{path}[{i}]"
        if not isinstance(ident, Mapping):
            out.append(Finding("NAME_INVALID", where))
            continue
        primary = ident.get("primaryIdentifier" if natural else "legalPersonName")
        kind = ident.get("nameIdentifierType" if natural else "legalPersonNameIdentifierType")
        if not _text(primary):
            out.append(Finding("NAME_MISSING", where))
        if kind not in NAME_TYPES:
            out.append(Finding("NAME_TYPE_INVALID", where))


def _person(person: Mapping[str, Any], path: str, out: list[Finding]) -> None:
    natural, legal = person.get("naturalPerson"), person.get("legalPerson")
    if (natural is None) == (legal is None):
        out.append(Finding("PERSON_KIND_INVALID", path))
        return
    if natural is not None:
        name = natural.get("name") if isinstance(natural, Mapping) else None
        _name_identifiers(
            name.get("nameIdentifier") if isinstance(name, Mapping) else None,
            f"{path}.naturalPerson.name",
            out,
            natural=True,
        )
    else:
        name = legal.get("name") if isinstance(legal, Mapping) else None
        _name_identifiers(
            name.get("nameIdentifier") if isinstance(name, Mapping) else None,
            f"{path}.legalPerson.name",
            out,
            natural=False,
        )
    body = natural if natural is not None else legal
    country = body.get("countryOfResidence") if isinstance(body, Mapping) else None
    if country is not None and not (isinstance(country, str) and COUNTRY.match(country)):
        out.append(Finding("COUNTRY_INVALID", f"{path}.countryOfResidence"))
    nat_id = body.get("nationalIdentification") if isinstance(body, Mapping) else None
    if isinstance(nat_id, Mapping):
        if nat_id.get("nationalIdentifierType") not in NATIONAL_ID_TYPES or not _text(
            nat_id.get("nationalIdentifier")
        ):
            out.append(Finding("NATIONAL_ID_INVALID", f"{path}.nationalIdentification"))
        elif nat_id.get("nationalIdentifierType") == "LEIX" and not lei_ok(
            str(nat_id["nationalIdentifier"])
        ):
            out.append(Finding("LEI_INVALID", f"{path}.nationalIdentification"))


def _vasp(block: Any, path: str, out: list[Finding]) -> str | None:
    """The VASP's LEI as stated, or None. Shape only."""
    person = block.get("legalPerson") if isinstance(block, Mapping) else None
    if not isinstance(person, Mapping):
        out.append(Finding("VASP_MISSING", path))
        return None
    _person({"legalPerson": person}, path, out)
    nat = person.get("nationalIdentification")
    lei = (
        nat.get("nationalIdentifier")
        if isinstance(nat, Mapping) and nat.get("nationalIdentifierType") == "LEIX"
        else None
    )
    return str(lei).upper() if isinstance(lei, str) else None


def _counterparty(lei: str | None, jurisdiction: str, registry: VaspRegistry) -> Counterparty:
    if not lei:
        return Counterparty.NOT_PROVIDED
    if not lei_ok(lei):
        return Counterparty.LEI_INVALID
    entry = registry.entries.get(lei)
    if entry is None:
        return Counterparty.UNVERIFIED_COUNTERPARTY
    if entry.status != "ACTIVE" or jurisdiction not in entry.jurisdictions:
        return Counterparty.NOT_ACTIVE_FOR_JURISDICTION
    return Counterparty.KNOWN


def threshold_for(
    registry: VaspRegistry, jurisdiction: str, amount: str, currency: str
) -> Threshold:
    """Whether the transfer is at or above the legal threshold. UNKNOWN when none is configured or the currency differs: never assumed below."""
    spec = registry.thresholds.get(jurisdiction)
    if spec is None or spec[1] != currency:
        return Threshold.UNKNOWN
    try:
        return Threshold.AT_OR_ABOVE if Decimal(amount) >= spec[0] else Threshold.BELOW
    except InvalidOperation:
        return Threshold.UNKNOWN


def assess(payload: Mapping[str, Any], registry: VaspRegistry, *, jurisdiction: str) -> Assessment:
    """Shape of the IVMS101 payload plus the counterparty and threshold questions. Findings carry a code and a path, never a value."""
    if not COUNTRY.match(jurisdiction):
        raise TravelRuleError("JURISDICTION_INVALID", jurisdiction)
    out: list[Finding] = []
    for i, p in enumerate(
        _persons(payload.get("originator"), "originatorPersons", "originator", out)
    ):
        _person(p, f"originator.originatorPersons[{i}]", out)
    for i, p in enumerate(
        _persons(payload.get("beneficiary"), "beneficiaryPersons", "beneficiary", out)
    ):
        _person(p, f"beneficiary.beneficiaryPersons[{i}]", out)
    for side, key in (("originator", "accountNumber"), ("beneficiary", "accountNumber")):
        block = payload.get(side)
        accounts = block.get(key) if isinstance(block, Mapping) else None
        if not (
            isinstance(accounts, Sequence)
            and not isinstance(accounts, str | bytes)
            and accounts
            and all(_text(a) for a in accounts)
        ):
            out.append(Finding("ACCOUNT_MISSING", f"{side}.{key}"))
    origin_lei = _vasp(payload.get("originatingVASP"), "originatingVASP", out)
    benef_lei = _vasp(payload.get("beneficiaryVASP"), "beneficiaryVASP", out)
    transfer = payload.get("transfer")
    amount = currency = ""
    if isinstance(transfer, Mapping):
        amount, currency = str(transfer.get("amount", "")), str(transfer.get("asset", "")).upper()
        try:
            if (
                not isinstance(transfer.get("amount"), str)
                or not Decimal(amount).is_finite()
                or Decimal(amount) <= 0
            ):
                out.append(
                    Finding("AMOUNT_INVALID", "transfer.amount")
                )  # a decimal string, never a number
        except InvalidOperation:
            out.append(Finding("AMOUNT_INVALID", "transfer.amount"))
        if not CURRENCY.match(currency):
            out.append(Finding("ASSET_INVALID", "transfer.asset"))
    else:
        out.append(Finding("BLOCK_MISSING", "transfer"))
    notes = (
        "Shape only: a well-formed name is not a verified person.",
        "Nothing was sent and nothing was stored. A person decides whether to release or hold the transfer.",
    )
    return Assessment(
        not out,
        tuple(out),
        _counterparty(origin_lei, jurisdiction, registry),
        _counterparty(benef_lei, jurisdiction, registry),
        threshold_for(registry, jurisdiction, amount, currency),
        notes,
        origin_lei,
        benef_lei,
    )
