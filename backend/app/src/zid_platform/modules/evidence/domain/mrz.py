"""Machine readable zone (MRZ) reading with ICAO 9303 check digits (ZID-GAP-001 v2.0 DTV-16; CR-073 section B). Pure: text in, fields out.

What this establishes, and what it does not:
- it reads the fields a travel document's MRZ carries and says, per field, whether the check digit that protects it holds;
- a failed check digit is reported by name, never repaired and never guessed (a digit OCR misread as another is exactly what the check
  digit is for, so the answer is "the line does not hold together", and a person or a better scan resolves it);
- passing every check digit proves the line is internally consistent. It does NOT prove the document is genuine: anyone can print a
  consistent MRZ. The result therefore always says `authenticity: NOT_ASSESSED`, and its source is MRZ, which is extraction only.
  Authenticity needs the chip (NFC) or an issuer check, neither of which is this module.

Formats: TD3 (passport, 2 lines of 44), TD2 (2 lines of 36) and TD1 (ID card, 3 lines of 30).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

_WEIGHTS = (7, 3, 1)
_FILLER = "<"
_VALID = re.compile(r"^[A-Z0-9<]+$")


class MrzError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def check_digit(data: str) -> int:
    """ICAO 9303 part 3: digits count as themselves, A-Z as 10-35, the filler as 0; weights 7, 3, 1 repeat; the sum modulo 10."""
    total = 0
    for i, ch in enumerate(data):
        if ch.isdigit():
            v = int(ch)
        elif "A" <= ch <= "Z":
            v = ord(ch) - ord("A") + 10
        elif ch == _FILLER:
            v = 0
        else:
            raise MrzError("MRZ_CHARACTER_INVALID", ch)
        total += v * _WEIGHTS[i % 3]
    return total % 10


@dataclass(frozen=True, slots=True)
class Checked:
    """One field and whether its own check digit holds (None: the format has no check digit for it)."""

    value: str
    check_ok: bool | None


@dataclass(frozen=True, slots=True)
class MrzResult:
    form: str  # TD1 | TD2 | TD3
    document_code: str
    issuing_state: str
    surname: str
    given_names: str
    document_number: Checked
    nationality: str
    birth_date: Checked  # YYMMDD as printed; `birth_date_iso` is the interpreted date
    sex: str
    expiry_date: Checked
    optional_data: str
    composite_ok: bool | None
    birth_date_iso: str | None = None
    expiry_date_iso: str | None = None
    reason_codes: tuple[str, ...] = field(default=())

    @property
    def all_checks_hold(self) -> bool:
        flags = [
            self.document_number.check_ok,
            self.birth_date.check_ok,
            self.expiry_date.check_ok,
            self.composite_ok,
        ]
        return all(f is not False for f in flags) and not self.reason_codes


def _clean(lines: list[str]) -> list[str]:
    out = [ln.strip().replace(" ", "").upper() for ln in lines if ln and ln.strip()]
    for ln in out:
        if not _VALID.match(ln):
            bad = next(c for c in ln if not re.match(r"[A-Z0-9<]", c))
            raise MrzError("MRZ_CHARACTER_INVALID", bad)
    return out


def _names(field_: str) -> tuple[str, str]:
    surname, _, given = field_.partition("<<")
    clean = lambda s: " ".join(p for p in s.replace("<", " ").split())
    return clean(surname), clean(given)


def _dated(yymmdd: str, *, future_ok: bool, today: date) -> str | None:
    """YYMMDD to an ISO date. The century is not in the MRZ: a birth date is the most recent past year that fits, an expiry date is
    taken within 50 years either side of today. None when the digits are not a real calendar date."""
    if not re.fullmatch(r"\d{6}", yymmdd):
        return None
    yy, mm, dd = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:])
    candidates = [
        c * 100 + yy for c in (today.year // 100 - 1, today.year // 100, today.year // 100 + 1)
    ]
    chosen: int | None = None
    if future_ok:  # expiry: nearest to today
        chosen = min(candidates, key=lambda y: abs(y - today.year))
    else:  # birth: not in the future
        past = [y for y in candidates if y <= today.year]
        chosen = max(past) if past else None
    if chosen is None:
        return None
    try:
        return date(chosen, mm, dd).isoformat()
    except ValueError:
        return None


def _field(value: str, digit: str) -> Checked:
    ok = digit.isdigit() and check_digit(value) == int(digit)
    return Checked(value.rstrip(_FILLER), ok)


def parse(lines: list[str], *, today: date | None = None) -> MrzResult:
    today = today or date.today()
    ls = _clean(lines)
    if len(ls) == 2 and all(len(x) == 44 for x in ls):
        return _td3(ls, today)
    if len(ls) == 2 and all(len(x) == 36 for x in ls):
        return _td2(ls, today)
    if len(ls) == 3 and all(len(x) == 30 for x in ls):
        return _td1(ls, today)
    raise MrzError("MRZ_FORMAT_UNKNOWN", f"{len(ls)} lines of {[len(x) for x in ls]}")


def _finish(
    form: str,
    code: str,
    state: str,
    names: tuple[str, str],
    number: Checked,
    nationality: str,
    birth: Checked,
    sex: str,
    expiry: Checked,
    optional: str,
    composite: bool | None,
    today: date,
) -> MrzResult:
    reasons: list[str] = []
    if number.check_ok is False:
        reasons.append("DOCUMENT_NUMBER_CHECK_DIGIT_FAILED")
    if birth.check_ok is False:
        reasons.append("BIRTH_DATE_CHECK_DIGIT_FAILED")
    if expiry.check_ok is False:
        reasons.append("EXPIRY_DATE_CHECK_DIGIT_FAILED")
    if composite is False:
        reasons.append("COMPOSITE_CHECK_DIGIT_FAILED")
    birth_iso = _dated(birth.value, future_ok=False, today=today)
    expiry_iso = _dated(expiry.value, future_ok=True, today=today)
    if birth_iso is None:
        reasons.append("BIRTH_DATE_NOT_A_DATE")
    if expiry_iso is None:
        reasons.append("EXPIRY_DATE_NOT_A_DATE")
    if sex not in {"M", "F", "<"}:
        reasons.append("SEX_NOT_RECOGNISED")
    return MrzResult(
        form, code.rstrip(_FILLER), state.rstrip(_FILLER), names[0], names[1], number,
        nationality.rstrip(_FILLER), birth, "X" if sex == "<" else sex, expiry,
        optional.rstrip(_FILLER), composite, birth_iso, expiry_iso, tuple(reasons),
    )  # fmt: skip


def _td3(ls: list[str], today: date) -> MrzResult:
    a, b = ls
    composite_data = b[0:10] + b[13:20] + b[21:43]
    return _finish(
        "TD3", a[0:2], a[2:5], _names(a[5:44]),
        _field(b[0:9], b[9]), b[10:13], _field(b[13:19], b[19]), b[20],
        _field(b[21:27], b[27]), b[28:42],
        b[43].isdigit() and check_digit(composite_data) == int(b[43]), today,
    )  # fmt: skip


def _td2(ls: list[str], today: date) -> MrzResult:
    a, b = ls
    composite_data = b[0:10] + b[13:20] + b[21:35]
    return _finish(
        "TD2", a[0:2], a[2:5], _names(a[5:36]),
        _field(b[0:9], b[9]), b[10:13], _field(b[13:19], b[19]), b[20],
        _field(b[21:27], b[27]), b[28:35],
        b[35].isdigit() and check_digit(composite_data) == int(b[35]), today,
    )  # fmt: skip


def _td1(ls: list[str], today: date) -> MrzResult:
    a, b, c = ls
    composite_data = a[5:30] + b[0:7] + b[8:15] + b[18:29]
    return _finish(
        "TD1", a[0:2], a[2:5], _names(c[0:30]),
        _field(a[5:14], a[14]), b[15:18], _field(b[0:6], b[6]), b[7],
        _field(b[8:14], b[14]), (a[15:30] + b[18:29]),
        b[29].isdigit() and check_digit(composite_data) == int(b[29]), today,
    )  # fmt: skip
