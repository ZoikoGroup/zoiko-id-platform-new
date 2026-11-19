"""Details the person typed, compared with what the platform itself read from the document photograph (the face-match route, CR-129).

A pure function over text: no network, no image, nothing kept. The reading is a free-text OCR of the whole picture, which is noisy, so the answer for each field is one of three, and only the third is
ever held against the person:

    MATCH      what was typed is on the document
    NOT_READ   the reading was too poor, or the field was not typed, to say either way: it counts for nothing and against nobody
    MISMATCH   the reading is good (a lot of text came back) and what was typed is not in it

A match is a consistency check on the document, not proof of anything: the person who types the details is the person who chose the document, so it never lowers the face threshold."""

from __future__ import annotations

import re
from datetime import date
from difflib import SequenceMatcher

MATCH = "MATCH"
MISMATCH = "MISMATCH"
NOT_READ = "NOT_READ"
GOOD_READING = 60  # characters of text: below this the picture was not read well enough to hold anything against anyone
_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
_LOOK_ALIKE = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8"})


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^A-Z]+", text.upper()) if len(w) >= 2]


def _name(text: str, typed: str) -> str:
    wanted = _words(typed)
    if not wanted:
        return NOT_READ
    seen = _words(text)
    if len(text) < GOOD_READING or not seen:
        return NOT_READ
    found = sum(1 for w in wanted if any(SequenceMatcher(None, w, s).ratio() >= 0.8 for s in seen))
    if found == len(wanted) or (len(wanted) >= 3 and found >= len(wanted) - 1):
        return MATCH
    return MISMATCH if found / len(wanted) < 0.5 else NOT_READ


def _fold(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper()).translate(_LOOK_ALIKE)


def _number(text: str, typed: str) -> str:
    wanted = _fold(typed)
    if len(wanted) < 4:
        return NOT_READ
    seen = _fold(text)
    if len(text) < GOOD_READING:
        return NOT_READ
    if wanted in seen:
        return MATCH
    # an Aadhaar card may be shown with its first eight digits covered, so the last four decide
    if len(wanted) == 12 and wanted[-4:] in seen:
        return MATCH
    return MISMATCH


def _birth(text: str, typed: date) -> str:
    if len(text) < GOOD_READING:
        return NOT_READ
    upper = text.upper()
    d, m, y = f"{typed.day:02d}", f"{typed.month:02d}", f"{typed.year}"
    mon = _MONTHS[typed.month - 1]
    squeezed = re.sub(r"\s+", "", upper)
    for form in (f"{d}/{m}/{y}", f"{d}-{m}-{y}", f"{d}.{m}.{y}", f"{y}-{m}-{d}", f"{d}{mon}{y}", f"{d}{mon}{y[2:]}"):
        if form in squeezed:
            return MATCH
    if re.search(r"YEAR\s*OF\s*BIRTH|YOB", upper) and y in upper:
        return MATCH  # some cards print only the year
    return MISMATCH


def compare(text: str, *, name: str | None, number: str | None, birth_date: date | None) -> dict[str, str]:
    """The result for each detail that was typed; a detail not typed is absent."""
    out: dict[str, str] = {}
    if name and name.strip():
        out["name"] = _name(text, name)
    if number and number.strip():
        out["number"] = _number(text, number)
    if birth_date is not None:
        out["birth_date"] = _birth(text, birth_date)
    return out


def contradicted(result: dict[str, str]) -> list[str]:
    """The fields the document clearly contradicts."""
    return sorted(k for k, v in result.items() if v == MISMATCH)
