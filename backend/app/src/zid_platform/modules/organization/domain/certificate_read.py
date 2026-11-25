"""Reading a company certificate that an applicant uploaded, and checking it against what they claimed (company verification; CR-131).

A pure function over the text the platform's own OCR returned. It finds three things on a certificate of incorporation or a register extract (the company's name, its number, the date it was
incorporated) and compares the name and number with the claim.

What the answer is, and is not. A certificate the applicant supplies can be forged, and reading it proves only that the text on the picture agrees with the claim. So the answer is one of

    CONSISTENT    the number and the name on the certificate are the claimed ones
    CONTRADICTS   a number or a name that is clearly there is not the claimed one: a mistake, or another company's certificate
    NOT_ENOUGH    the picture could not be read well enough to say (OCR is noisy: a poor reading is held against nobody)

and it is evidence for a reviewer and a check on the applicant's own typing. It never makes a company verified: that is the register's (or an acknowledged LEI record's) to say."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from zid_platform.modules.organization.domain.kyb import name_similarity

CONSISTENT = "CONSISTENT"
CONTRADICTS = "CONTRADICTS"
NOT_ENOUGH = "NOT_ENOUGH"
MATCH = "MATCH"
MISMATCH = "MISMATCH"
NOT_READ = "NOT_READ"
MIN_TEXT = 60  # characters: less than this is a picture that was not read
NAME_MATCH = 0.85
NAME_MISMATCH = 0.6  # between the two the reading is too uncertain to hold against anyone
_MONTHS = {m: i for i, m in enumerate(["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"], 1)}

_NUMBER = re.compile(r"(?:COMPANY|REGISTERED|REGISTRATION)\s*(?:NUMBER|NO\.?)\s*[:.\-]?\s*([A-Z]{0,2}\s?\d[\d\s]{4,9}\d)", re.IGNORECASE)
_NAME = re.compile(r"CERTIFIES\s+THAT\s+(.{2,160}?)\s+(?:IS\s+THIS\s+DAY|HAS\s+THIS\s+DAY|WAS\s+INCORPORATED|IS\s+INCORPORATED)", re.IGNORECASE | re.DOTALL)
_DATE = re.compile(r"(?:GIVEN\s+AT[^.\n]{0,60}?\s+ON|INCORPORATED\s+ON|DATE\s+OF\s+INCORPORATION)\s*[:.\-]?\s*(?:THE\s+)?(\d{1,2})(?:ST|ND|RD|TH)?\s+([A-Z]+)\s+(\d{4})", re.IGNORECASE)


@dataclass(frozen=True)
class Extracted:
    document: str  # CERTIFICATE_OF_INCORPORATION | UNKNOWN
    company_name: str | None
    company_number: str | None
    incorporated_on: date | None


def _squeeze(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text.replace("\r", "")).strip()


def extract(text: str) -> Extracted:
    t = _squeeze(text)
    upper = t.upper()
    number = None
    if (m := _NUMBER.search(t)) is not None:
        number = re.sub(r"\s+", "", m.group(1)).upper()
    name = None
    if (m := _NAME.search(t)) is not None:
        name = re.sub(r"\s+", " ", m.group(1)).strip(" ,.:;\"'")
    when = None
    if (m := _DATE.search(t)) is not None and (month := _MONTHS.get(m.group(2).upper())):
        try:
            when = date(int(m.group(3)), month, int(m.group(1)))
        except ValueError:
            when = None
    document = "CERTIFICATE_OF_INCORPORATION" if "CERTIFICATE OF INCORPORATION" in upper else "UNKNOWN"
    return Extracted(document, name or None, number, when)


def _fold(number: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", number.upper())


MIN_NUMBER_CHARS = 4  # a claimed number shorter than this is too weak to look for in free text


def _number_present(text: str, claimed_number: str) -> bool:
    """The claimed registration number written anywhere in the text, whatever the layout or language: its characters in order, with only spaces, dots, dashes or slashes between them, and not
    part of a longer number. The claim supplies the number, so no label ("Company Number", "Handelsregister", "SIREN", "CIN") has to be recognised."""
    chars = _fold(claimed_number)
    if len(chars) < MIN_NUMBER_CHARS:
        return False
    pattern = r"(?<![A-Z0-9])" + r"[\s.\-/]{0,2}".join(re.escape(c) for c in chars) + r"(?![A-Z0-9])"
    return re.search(pattern, text.upper()) is not None


def _name_present(text: str, claimed_name: str) -> bool:
    """The claimed company name written anywhere in the text: a run of words as long as the name (one more or one fewer allowed for a noisy reading) that is as close to it as a read name must be."""
    want = len(claimed_name.split())
    if want == 0:
        return False
    for line in text.splitlines():
        words = line.split()
        for size in {max(1, want - 1), want, want + 1}:
            for i in range(max(0, len(words) - size) + 1):
                if name_similarity(claimed_name, " ".join(words[i : i + size])) >= NAME_MATCH:
                    return True
    return False


def compare(text: str, *, claimed_name: str, claimed_number: str) -> dict[str, object]:
    """What the certificate says, and whether it agrees with the claim. Nothing but codes, the two numbers and the date it read: the text itself is never returned or kept."""
    got = extract(text)
    if len(text.strip()) < MIN_TEXT:
        return {"verdict": NOT_ENOUGH, "reading": "TOO_LITTLE_TEXT", "document": got.document, "number": NOT_READ, "name": NOT_READ, "incorporated_on": None}
    if _number_present(text, claimed_number) and _name_present(text, claimed_name):
        # CR-135: any country's certificate, in any layout: the claimed number and the claimed name are both written on it
        return {"verdict": CONSISTENT, "reading": "OK", "document": got.document, "number": MATCH, "name": MATCH,
                "incorporated_on": got.incorporated_on.isoformat() if got.incorporated_on else None}  # fmt: skip
    number = NOT_READ
    if got.company_number is not None:
        number = MATCH if _fold(got.company_number) == _fold(claimed_number) else MISMATCH
    name = NOT_READ
    if got.company_name is not None:
        s = name_similarity(claimed_name, got.company_name)
        name = MATCH if s >= NAME_MATCH else MISMATCH if s < NAME_MISMATCH else NOT_READ
    if MISMATCH in (number, name):
        verdict = CONTRADICTS
    elif number == MATCH and name == MATCH:
        verdict = CONSISTENT
    else:
        verdict = NOT_ENOUGH
    return {
        "verdict": verdict,
        "reading": "OK",
        "document": got.document,
        "number": number,
        "name": name,
        "incorporated_on": got.incorporated_on.isoformat() if got.incorporated_on else None,
    }
