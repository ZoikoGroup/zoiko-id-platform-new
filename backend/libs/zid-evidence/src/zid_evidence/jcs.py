"""RFC 8785 JSON Canonicalization Scheme (JCS), strict profile (EVIDENCE-001 §5; DEC-F2 §1).

`canonicalize` serializes a JSON value to its canonical UTF-8 bytes: object members sorted by
their UTF-16 code units, no whitespace, ECMAScript number formatting, minimal string escaping.
Ambiguous input is rejected rather than guessed at (EVID-04):
- non-finite numbers (NaN, +/-Infinity) and integers outside the IEEE-754 exact range (|n| > 2^53),
- non-string object keys, lone surrogates, values that are not JSON (dates, bytes, sets, ...).
`loads_strict` parses JSON text and additionally rejects duplicate member names and the
non-standard `NaN` / `Infinity` literals, so a canonical form can only come from one meaning.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

__all__ = ["MAX_SAFE_INTEGER", "CanonicalizationError", "canonicalize", "loads_strict"]

MAX_SAFE_INTEGER = 2**53  # integers beyond this cannot round-trip through an IEEE-754 double


class CanonicalizationError(ValueError):
    """EVIDENCE_CANONICALIZATION_FAILED: the payload cannot be canonicalized safely."""

    code = "EVIDENCE_CANONICALIZATION_FAILED"


_SHORT_ESCAPES = {
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0C: "\\f",
    0x0D: "\\r",
    0x22: '\\"',
    0x5C: "\\\\",
}


def _string(s: str) -> str:
    out = ['"']
    for ch in s:
        cp = ord(ch)
        if 0xD800 <= cp <= 0xDFFF:
            raise CanonicalizationError("lone surrogate in string")
        esc = _SHORT_ESCAPES.get(cp)
        if esc is not None:
            out.append(esc)
        elif cp < 0x20:
            out.append(f"\\u{cp:04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _number(value: float) -> str:
    """ECMAScript Number.prototype.toString (ECMA-262 §7.1.12.1), as RFC 8785 §3.2.2.3 requires."""
    if not math.isfinite(value):
        raise CanonicalizationError("non-finite number")
    if value == 0:
        return "0"  # also -0
    sign = "-" if value < 0 else ""
    # repr() is the shortest digit string that round-trips, as ECMAScript requires.
    _, digits_t, exp = Decimal(repr(abs(value))).as_tuple()
    assert isinstance(exp, int)
    digits = "".join(map(str, digits_t)).rstrip("0") or "0"
    exp += len("".join(map(str, digits_t))) - len(digits)
    k = len(digits)
    n = exp + k  # value = 0.digits * 10^n
    if k <= n <= 21:
        body = digits + "0" * (n - k)
    elif 0 < n <= 21:
        body = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        body = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        mantissa = digits if k == 1 else digits[0] + "." + digits[1:]
        body = f"{mantissa}e{'+' if e >= 0 else '-'}{abs(e)}"
    return sign + body


def _key_order(key: str) -> bytes:
    return key.encode("utf-16-be", "surrogatepass")


def _serialize(value: Any, out: list[str]) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise CanonicalizationError("integer outside the IEEE-754 exact range")
        out.append(_number(float(value)))
    elif isinstance(value, float):
        out.append(_number(value))
    elif isinstance(value, str):
        out.append(_string(value))
    elif isinstance(value, Mapping):
        keys = list(value.keys())
        if any(not isinstance(k, str) for k in keys):
            raise CanonicalizationError("object keys must be strings")
        out.append("{")
        for i, key in enumerate(sorted(keys, key=_key_order)):
            if i:
                out.append(",")
            out.append(_string(key))
            out.append(":")
            _serialize(value[key], out)
        out.append("}")
    elif isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _serialize(item, out)
        out.append("]")
    else:
        raise CanonicalizationError(f"not a JSON value: {type(value).__name__}")


def canonicalize(value: Any) -> bytes:
    """The RFC 8785 canonical UTF-8 bytes of a JSON value; raises CanonicalizationError."""
    out: list[str] = []
    _serialize(value, out)
    return "".join(out).encode("utf-8")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, val in pairs:
        if key in obj:
            raise CanonicalizationError(f"duplicate member name {key!r}")
        obj[key] = val
    return obj


def _reject_constant(name: str) -> Any:
    raise CanonicalizationError(f"non-standard JSON literal {name}")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise CanonicalizationError("number overflows an IEEE-754 double")
    return value


def loads_strict(data: str | bytes) -> Any:
    """Parse JSON for evidence use: duplicate keys, NaN/Infinity and overflowing numbers rejected."""
    try:
        return json.loads(
            data,
            object_pairs_hook=_no_duplicates,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except CanonicalizationError:
        raise
    except (ValueError, UnicodeDecodeError) as e:
        raise CanonicalizationError(f"invalid JSON: {e}") from e
