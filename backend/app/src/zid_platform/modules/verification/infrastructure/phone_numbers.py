"""Phone numbers through libphonenumber (ZID-DTV-WF-05 §04 phone pattern, A02; CR-114). Format, region and calling code only: libphonenumber
cannot establish allocation, reachability or ownership, and nothing here claims them."""

from __future__ import annotations

import phonenumbers

from zid_platform.modules.verification.domain.contact_challenges import (
    ChallengeError,
    ContactKind,
    Normalized,
    mask_phone,
    normalize_email,
)


def normalize_phone(raw: str, region: str | None) -> Normalized:
    text = raw.strip()
    if not text or len(text) > 32 or any(ord(c) < 32 for c in text):
        raise ChallengeError("PHONE_INVALID")
    try:
        n = phonenumbers.parse(text, None if text.startswith("+") else (region or "").upper())
    except phonenumbers.NumberParseException as e:
        raise ChallengeError("PHONE_INVALID", "not a phone number for that country") from e
    if not phonenumbers.is_possible_number(n):
        raise ChallengeError("PHONE_INVALID", "not a possible number")
    e164 = phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.E164)
    return Normalized(
        ContactKind.PHONE, e164, mask_phone(e164), phonenumbers.region_code_for_number(n),
        str(n.country_code), unusual=not phonenumbers.is_valid_number(n),
    )  # fmt: skip


def normalize(kind: ContactKind, raw: str, region: str | None = None) -> Normalized:
    return normalize_phone(raw, region) if kind is ContactKind.PHONE else normalize_email(raw)


__all__ = ["normalize", "normalize_phone"]
