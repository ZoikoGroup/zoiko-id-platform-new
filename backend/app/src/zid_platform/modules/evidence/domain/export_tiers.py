"""Disclosure tiers for evidence exports, with a named recipient (ZID-CP-WF08 v2.0 E06, section 08.1; CR-170). Pure domain, no I/O.

Three tiers, each a different promise about what leaves the platform:

    PUBLIC_MINIMAL      proof of integrity only: the receipt body is withheld, the proof and keys travel. Anyone may ask; no recipient is recorded.
    CUSTOMER_EVIDENCE   the receipt with its sensitive fields withheld, for the customer or a person it concerns.
    AUDITOR_DOSSIER     the full receipt for a named auditor or regulator. Needs a second person's approval before anything is issued.

An anonymous caller can only ever receive the public tier; there is no way to ask for a private one without signing in. A private tier names the recipient,
the purpose, the legal basis and how long it may be kept, and one that crosses a border also needs a second person.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

TIERS = ("PUBLIC_MINIMAL", "CUSTOMER_EVIDENCE", "AUDITOR_DOSSIER")
RECIPIENT_KINDS = ("CUSTOMER", "DATA_SUBJECT", "AUDITOR", "REGULATOR", "COUNSEL")
LEGAL_BASES = (
    "CONSENT",
    "CONTRACT",
    "LEGAL_OBLIGATION",
    "LEGITIMATE_INTEREST",
    "AUDIT_ENGAGEMENT",
    "COURT_OR_REGULATOR_ORDER",
)
MAX_RETENTION_DAYS = {"PUBLIC_MINIMAL": 30, "CUSTOMER_EVIDENCE": 90, "AUDITOR_DOSSIER": 365}
RECIPIENT_KINDS_FOR = {
    "CUSTOMER_EVIDENCE": ("CUSTOMER", "DATA_SUBJECT"),
    "AUDITOR_DOSSIER": ("AUDITOR", "REGULATOR", "COUNSEL"),
}
_COUNTRY = re.compile(r"^[A-Z]{2}$")

# What the tier hands over. The existing export package builder takes a mode and a list of fields to withhold.
EXPORT_MODE = {
    "PUBLIC_MINIMAL": "CUSTOMER_VERIFY",
    "CUSTOMER_EVIDENCE": "CUSTOMER_VERIFY",
    "AUDITOR_DOSSIER": "AUDITOR",
}
PUBLIC_VISIBLE = (
    "receipt_id",
    "schema_version",
    "evidence_class",
    "decision_type",
)  # all the public tier shows of the receipt body
CUSTOMER_WITHHELD = (
    "actor_handle",
    "input_commitment",
    "material_object_id",
)  # the customer sees the decision, not the internal handles behind it


def withheld_for(tier: str, body_keys: Iterable[str]) -> list[str]:
    """The receipt fields a tier keeps back, among those the receipt actually has (a field it lacks cannot be withheld)."""
    keys = sorted(set(body_keys))
    if tier == "PUBLIC_MINIMAL":
        return [k for k in keys if k not in PUBLIC_VISIBLE]
    if tier == "CUSTOMER_EVIDENCE":
        return [k for k in keys if k in CUSTOMER_WITHHELD]
    return []


def needs_second_person(tier: str, *, cross_border: bool) -> bool:
    return tier == "AUDITOR_DOSSIER" or (tier != "PUBLIC_MINIMAL" and cross_border)


def request_problems(
    *, tier: str, authenticated: bool, recipient: Mapping[str, Any] | None, purpose: str, legal_basis: str | None, retention_days: int,
    recipient_country: str | None,
) -> list[str]:  # fmt: skip
    """Every reason a request cannot go ahead, named. Empty means it may be recorded (not that it is approved)."""
    if tier not in TIERS:
        return ["TIER_UNKNOWN"]
    problems: list[str] = []
    if not authenticated and tier != "PUBLIC_MINIMAL":
        return ["PRIVATE_TIER_NEEDS_SIGN_IN"]  # an anonymous caller cannot pick a private tier
    if retention_days < 1 or retention_days > MAX_RETENTION_DAYS[tier]:
        problems.append("RETENTION_OUT_OF_RANGE")
    if tier == "PUBLIC_MINIMAL":
        return problems
    if len(purpose.strip()) < 10:
        problems.append("PURPOSE_REQUIRED")
    if legal_basis not in LEGAL_BASES:
        problems.append("LEGAL_BASIS_REQUIRED")
    if (
        not recipient
        or not str(recipient.get("name", "")).strip()
        or len(str(recipient.get("name", ""))) > 200
    ):
        problems.append("RECIPIENT_NAME_REQUIRED")
    elif recipient.get("kind") not in RECIPIENT_KINDS_FOR[tier]:
        problems.append("RECIPIENT_KIND_NOT_ALLOWED_FOR_TIER")
    if recipient and not str(recipient.get("contact", "")).strip():
        problems.append("RECIPIENT_CONTACT_REQUIRED")
    if recipient_country is None or not _COUNTRY.match(recipient_country):
        problems.append(
            "RECIPIENT_COUNTRY_REQUIRED"
        )  # the cross-border question cannot be skipped by leaving it out
    if (
        tier == "AUDITOR_DOSSIER"
        and legal_basis in LEGAL_BASES
        and legal_basis in ("CONSENT", "LEGITIMATE_INTEREST")
    ):  # a full dossier needs more than a preference
        problems.append("LEGAL_BASIS_NOT_ALLOWED_FOR_TIER")
    return problems


def is_cross_border(tenant_country: str, recipient_country: str | None) -> bool:
    return recipient_country is not None and recipient_country.upper() != tenant_country.upper()
