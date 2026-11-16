"""Payment method ownership assertion (ZID-GAP-001 v2.0 DTV-27; CR-101). Pure domain.

A payment processor that has tokenized a card or bank debit can state that the method belongs to the person who set it up, and what its own checks said. This
module takes that statement as a CLAIM and keeps Zoiko iD outside the card data boundary.

- the PCI boundary is a hard rule: a payload that carries a card number (any 13 to 19 digit run that passes the Luhn check, with or without spaces or dashes), a
  card verification value, or an expiry as data is refused whole (`CARD_DATA_REFUSED`) and never echoed. Only a token reference travels;
- what the processor stated is passed through and labelled PROVIDER_ASSERTED. Zoiko iD never says "verified" about a card;
- a prepaid or unknown funding type is a weak basis for ownership and says so; it is a note for a person, not a refusal;
- an assertion that the method belongs to someone else, or one the processor could not check, is not a pass.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

LINE = "payment_ownership"
CHECKS = frozenset({"PASS", "FAIL", "NOT_CHECKED"})
FUNDING = frozenset({"CREDIT", "DEBIT", "PREPAID", "BANK_ACCOUNT", "UNKNOWN"})
BELONGS = frozenset({"YES", "NO", "UNKNOWN"})
ALLOWED_KEYS = frozenset(
    {
        "processor",
        "payment_method_ref",
        "belongs_to_subject",
        "cvc_check",
        "postcode_check",
        "three_ds",
        "funding",
        "country",
    }
)
FORBIDDEN_KEYS = re.compile(
    r"pan\b|card_?number|cvv|cvc_?value|security_?code|expiry|exp_(month|year)|track|magstripe|account_?number|iban",
    re.IGNORECASE,
)
REF = re.compile(r"^(pm|tok|card|src)_[A-Za-z0-9]{8,64}$")
DIGIT_RUN = re.compile(r"(?<![0-9])(?:[0-9][ -]?){12,18}[0-9](?![0-9])")


class PaymentError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class Outcome(StrEnum):
    OWNERSHIP_ASSERTED = "OWNERSHIP_ASSERTED"
    OWNERSHIP_NEEDS_REVIEW = "OWNERSHIP_NEEDS_REVIEW"
    OWNERSHIP_DENIED_BY_PROCESSOR = "OWNERSHIP_DENIED_BY_PROCESSOR"
    NOT_AVAILABLE = "NOT_AVAILABLE"


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


def _contains_card_number(value: Any) -> bool:
    """True when any string anywhere in the payload holds a plausible card number. The value is never returned or logged."""
    if isinstance(value, str):
        return any(luhn_ok(re.sub(r"[ -]", "", m.group(0))) for m in DIGIT_RUN.finditer(value))
    if isinstance(value, Mapping):
        return any(_contains_card_number(v) for v in value.values())
    if isinstance(value, list | tuple):
        return any(_contains_card_number(v) for v in value)
    return False


@dataclass(frozen=True, slots=True)
class Assertion:
    processor: str
    payment_method_ref: str
    belongs_to_subject: str
    cvc_check: str
    postcode_check: str
    three_ds: str
    funding: str
    country: str


def parse_assertion(payload: Mapping[str, Any]) -> Assertion:
    """Refuse card data first (before anything is read or echoed), then check the shape of the processor's statement."""
    if any(FORBIDDEN_KEYS.search(str(k)) for k in payload) or _contains_card_number(dict(payload)):
        raise PaymentError(
            "CARD_DATA_REFUSED", "the payload carries card data; send a token reference only"
        )
    unknown = set(payload) - ALLOWED_KEYS
    if unknown:
        raise PaymentError("ASSERTION_KEY_UNKNOWN", ",".join(sorted(unknown)))
    ref = str(payload.get("payment_method_ref", ""))
    if not REF.match(ref) or not str(payload.get("processor", "")).strip():
        raise PaymentError("ASSERTION_INVALID", "processor or payment_method_ref")
    belongs = str(payload.get("belongs_to_subject", "UNKNOWN"))
    funding = str(payload.get("funding", "UNKNOWN"))
    checks = [
        str(payload.get(k, "NOT_CHECKED")) for k in ("cvc_check", "postcode_check", "three_ds")
    ]
    country = str(payload.get("country", ""))
    if (
        belongs not in BELONGS
        or funding not in FUNDING
        or any(c not in CHECKS for c in checks)
        or (country and not re.fullmatch(r"[A-Z]{2}", country))
    ):
        raise PaymentError("ASSERTION_INVALID", "an enumerated field")
    return Assertion(
        str(payload["processor"]).strip(),
        ref,
        belongs,
        checks[0],
        checks[1],
        checks[2],
        funding,
        country,
    )


@dataclass(frozen=True, slots=True)
class Claim:
    outcome: Outcome
    reasons: tuple[str, ...]
    assurance: str = "PROVIDER_ASSERTED"
    recommendation: str = "REVIEW"
    notes: tuple[str, ...] = (
        "The processor stated this; Zoiko iD holds no card data and does not call a card verified.",
        "Ownership of a payment method says nothing about who the person is.",
    )


def to_claim(a: Assertion) -> Claim:
    """What the processor's statement supports. A failed check or a denial is reported; none of it declines anyone."""
    if a.belongs_to_subject == "NO":
        return Claim(Outcome.OWNERSHIP_DENIED_BY_PROCESSOR, ("PROCESSOR_STATES_NOT_THE_HOLDER",))
    if a.belongs_to_subject == "UNKNOWN":
        return Claim(Outcome.NOT_AVAILABLE, ("PROCESSOR_COULD_NOT_STATE_OWNERSHIP",))
    reasons = ["PROCESSOR_STATES_HOLDER"]
    weak = False
    if a.cvc_check == "FAIL" or a.postcode_check == "FAIL" or a.three_ds == "FAIL":
        return Claim(Outcome.OWNERSHIP_NEEDS_REVIEW, ("A_PROCESSOR_CHECK_FAILED",))
    if a.funding in {"PREPAID", "UNKNOWN"}:
        reasons.append("FUNDING_TYPE_IS_A_WEAK_BASIS")
        weak = True
    if "NOT_CHECKED" in (a.cvc_check, a.postcode_check, a.three_ds):
        reasons.append("SOME_PROCESSOR_CHECKS_NOT_RUN")
    return Claim(
        Outcome.OWNERSHIP_NEEDS_REVIEW if weak else Outcome.OWNERSHIP_ASSERTED, tuple(reasons)
    )
