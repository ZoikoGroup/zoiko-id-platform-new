"""Open banking account-ownership assertion (ZID-GAP-001 v2.0 DTV-26; CR-101). Pure domain.

An authorised account information provider (an aggregator) can tell a relying party that the person holds an account in their own name. That needs a licensed
partner and the person's consent, and the product line ships as DESIGN. What is here is the part that is ours: refuse account data, require a live consent,
and say who asserted what.

- the assertion is a CLAIM, never account data. A payload that carries a balance, transactions, an account number, an IBAN, a sort code or a card number is
  refused whole (`ACCOUNT_DATA_REFUSED`) and not echoed: Zoiko iD never holds what a bank holds;
- the strongest answer is ASSERTED_BY_LICENSED_PROVIDER, and only when the provider is in a registry the operators control, ACTIVE, and licensed for the
  jurisdiction. An unlisted provider is PROVIDER_NOT_LICENSED, however plausible its payload;
- consent has a purpose, an end, and a maximum lifetime; a lapsed or open-ended consent is refused;
- a name that only partly matches is REVIEW for a person, never a pass and never a decline;
- the assertion is provider-stated: the answer says PROVIDER_ASSERTED, not VERIFIED, and nothing here contacts a bank.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

LINE = "open_banking_ownership"
MAX_CONSENT_DAYS = 90
SCOPES = frozenset({"ACCOUNT_OWNERSHIP"})  # nothing wider is ever asked for
NAME_MATCH = frozenset({"MATCH", "PARTIAL", "NO_MATCH", "UNAVAILABLE"})
ALLOWED_KEYS = frozenset(
    {"provider_id", "consent_ref", "purpose", "scope", "holder_name_match", "account_ref_hash", "consent_expires_at", "obtained_at"}
)  # fmt: skip
ACCOUNT_DATA = re.compile(
    r"balance|transaction|iban|account_?number|sort_?code|routing|pan\b|card_?number|cvv|cvc|statement",
    re.IGNORECASE,
)
HASH = re.compile(r"^[0-9a-f]{64}$")
PURPOSE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


class OpenBankingError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class Outcome(StrEnum):
    ASSERTED_BY_LICENSED_PROVIDER = "ASSERTED_BY_LICENSED_PROVIDER"
    PROVIDER_NOT_LICENSED = "PROVIDER_NOT_LICENSED"
    CONSENT_EXPIRED = "CONSENT_EXPIRED"
    NAME_NEEDS_REVIEW = "NAME_NEEDS_REVIEW"
    NAME_DOES_NOT_MATCH = "NAME_DOES_NOT_MATCH"
    NOT_AVAILABLE = "NOT_AVAILABLE"


@dataclass(frozen=True, slots=True)
class LicensedProvider:
    provider_id: str
    status: str  # ACTIVE | SUSPENDED | WITHDRAWN
    jurisdictions: frozenset[str]
    licence_ref: str


@dataclass(frozen=True, slots=True)
class ProviderRegistry:
    version: str
    providers: Mapping[str, LicensedProvider]


def parse_registry(doc: Mapping[str, Any]) -> ProviderRegistry:
    out: dict[str, LicensedProvider] = {}
    for item in doc.get("providers") or []:
        pid = str(item.get("provider_id", ""))
        countries = frozenset(str(c).upper() for c in item.get("jurisdictions") or ())
        if (
            not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", pid)
            or pid in out
            or not countries
            or not item.get("licence_ref")
        ):
            raise OpenBankingError("REGISTRY_INVALID", pid or "provider")
        if item.get("status") not in {"ACTIVE", "SUSPENDED", "WITHDRAWN"} or any(
            not re.fullmatch(r"[A-Z]{2}", c) for c in countries
        ):
            raise OpenBankingError("REGISTRY_INVALID", pid)
        out[pid] = LicensedProvider(pid, str(item["status"]), countries, str(item["licence_ref"]))
    return ProviderRegistry(str(doc.get("version", "")), out)


@dataclass(frozen=True, slots=True)
class Assertion:
    provider_id: str
    consent_ref: str
    purpose: str
    holder_name_match: str
    account_ref_hash: str
    consent_expires_at: datetime
    obtained_at: datetime


def _when(value: Any, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as e:
        raise OpenBankingError("ASSERTION_INVALID", name) from e
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_assertion(payload: Mapping[str, Any]) -> Assertion:
    """A well-formed ownership assertion, or an error. Anything that looks like account data refuses the whole payload and is never echoed."""
    for key in payload:
        if ACCOUNT_DATA.search(str(key)):
            raise OpenBankingError("ACCOUNT_DATA_REFUSED", "the payload carries account data")
    unknown = set(payload) - ALLOWED_KEYS
    if unknown:
        raise OpenBankingError("ASSERTION_KEY_UNKNOWN", ",".join(sorted(unknown)))
    if payload.get("scope") not in SCOPES:
        raise OpenBankingError("SCOPE_NOT_ALLOWED", "only ACCOUNT_OWNERSHIP")
    if payload.get("holder_name_match") not in NAME_MATCH:
        raise OpenBankingError("ASSERTION_INVALID", "holder_name_match")
    if not HASH.match(str(payload.get("account_ref_hash", ""))) or not PURPOSE.match(
        str(payload.get("purpose", ""))
    ):
        raise OpenBankingError("ASSERTION_INVALID", "account_ref_hash or purpose")
    if not str(payload.get("provider_id", "")) or not str(payload.get("consent_ref", "")):
        raise OpenBankingError("ASSERTION_INVALID", "provider_id or consent_ref")
    return Assertion(
        str(payload["provider_id"]),
        str(payload["consent_ref"]),
        str(payload["purpose"]),
        str(payload["holder_name_match"]),
        str(payload["account_ref_hash"]),
        _when(payload.get("consent_expires_at"), "consent_expires_at"),
        _when(payload.get("obtained_at"), "obtained_at"),
    )


@dataclass(frozen=True, slots=True)
class Claim:
    outcome: Outcome
    reasons: tuple[str, ...]
    assurance: str = "PROVIDER_ASSERTED"
    recommendation: str = "REVIEW"  # a person decides what to do with an ownership claim
    notes: tuple[str, ...] = (
        "A provider asserted this; Zoiko iD did not contact a bank and holds no account data.",
        "Ownership of one account says nothing about who the person is.",
    )


def to_claim(
    a: Assertion, registry: ProviderRegistry, *, jurisdiction: str, now: datetime
) -> Claim:
    """What the assertion supports. The order matters: licence first, then consent, then the name, so a refusal names the first thing that is wrong."""
    provider = registry.providers.get(a.provider_id)
    if (
        provider is None
        or provider.status != "ACTIVE"
        or jurisdiction.upper() not in provider.jurisdictions
    ):
        return Claim(
            Outcome.PROVIDER_NOT_LICENSED, ("PROVIDER_NOT_LISTED_ACTIVE_FOR_JURISDICTION",)
        )
    if a.consent_expires_at <= now or a.consent_expires_at - a.obtained_at > timedelta(
        days=MAX_CONSENT_DAYS
    ):
        return Claim(Outcome.CONSENT_EXPIRED, ("CONSENT_LAPSED_OR_TOO_LONG",))
    if a.holder_name_match == "MATCH":
        return Claim(
            Outcome.ASSERTED_BY_LICENSED_PROVIDER, ("PROVIDER_STATES_HOLDER_NAME_MATCHES",)
        )
    if a.holder_name_match == "PARTIAL":
        return Claim(Outcome.NAME_NEEDS_REVIEW, ("HOLDER_NAME_PARTIAL",))
    if a.holder_name_match == "NO_MATCH":
        return Claim(Outcome.NAME_DOES_NOT_MATCH, ("HOLDER_NAME_DIFFERS",))
    return Claim(Outcome.NOT_AVAILABLE, ("PROVIDER_DID_NOT_COMPARE_NAMES",))
