"""RFC 8785 canonicalization and algorithm-tagged digests (EVID-03, EVID-04; DEC-F2 §1)."""

from __future__ import annotations

import hashlib
import struct

import pytest
from zid_evidence import (
    CanonicalizationError,
    Digest,
    DigestError,
    canonicalize,
    hash_bytes,
    hash_json,
    loads_strict,
)

# RFC 8785 Appendix B: IEEE-754 bit patterns and their canonical number text.
NUMBER_VECTORS = [
    ("0000000000000000", "0"),
    ("8000000000000000", "0"),
    ("0000000000000001", "5e-324"),
    ("8000000000000001", "-5e-324"),
    ("7fefffffffffffff", "1.7976931348623157e+308"),
    ("ffefffffffffffff", "-1.7976931348623157e+308"),
    ("4340000000000000", "9007199254740992"),
    ("c340000000000000", "-9007199254740992"),
    ("4430000000000000", "295147905179352830000"),
    ("44b52d02c7e14af5", "9.999999999999997e+22"),
    ("44b52d02c7e14af6", "1e+23"),
    ("44b52d02c7e14af7", "1.0000000000000001e+23"),
    ("444b1ae4d6e2ef4e", "999999999999999700000"),
    ("444b1ae4d6e2ef4f", "999999999999999900000"),
    ("444b1ae4d6e2ef50", "1e+21"),
    ("3eb0c6f7a0b5ed8c", "9.999999999999997e-7"),
    ("3eb0c6f7a0b5ed8d", "0.000001"),
    ("41b3de4355555553", "333333333.3333332"),
    ("41b3de4355555554", "333333333.33333325"),
    ("41b3de4355555555", "333333333.3333333"),
    ("41b3de4355555556", "333333333.3333334"),
    ("41b3de4355555557", "333333333.33333343"),
    ("becbf647612f3696", "-0.0000033333333333333333"),
    ("43143ff3c1cb0959", "1424953923781206.2"),
]


def _double(bits: str) -> float:
    return float(struct.unpack(">d", bytes.fromhex(bits))[0])


@pytest.mark.parametrize(("bits", "expected"), NUMBER_VECTORS)
def test_rfc8785_number_serialization(bits: str, expected: str) -> None:
    assert canonicalize(_double(bits)) == expected.encode()


@pytest.mark.parametrize("bits", ["7fffffffffffffff", "7ff0000000000000", "fff0000000000000"])
def test_non_finite_numbers_are_rejected(bits: str) -> None:
    with pytest.raises(CanonicalizationError):
        canonicalize(_double(bits))


def test_rfc8785_section_3_2_2_example() -> None:
    """RFC 8785 §3.2.2: whitespace, number and string normalization in one document."""
    text = (
        '{"numbers": [333333333.33333329, 1E30, 4.50, 2e-3, 0.000000000000000000000000001],'
        ' "string": "\\u20ac$\\u000F\\u000aA\'\\u0042\\u0022\\u005c\\\\\\"\\/",'
        ' "literals": [null, true, false]}'
    )
    expected = (
        '{"literals":[null,true,false],"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
        '"string":"€$\\u000f\\nA\'B\\"\\\\\\\\\\"/"}'
    )
    assert canonicalize(loads_strict(text)) == expected.encode("utf-8")


def test_rfc8785_section_3_2_3_member_sorting_by_utf16_code_units() -> None:
    value = {
        "€": "Euro Sign",
        "\r": "Carriage Return",
        "דּ": "Hebrew Letter Dalet With Dagesh",
        "1": "One",
        "\U0001f600": "Emoji: Grinning Face",
        "\u0080": "Control",
        "ö": "Latin Small Letter O With Diaeresis",
    }
    out = canonicalize(value).decode("utf-8")
    order = ["\\r", "1", "\u0080", "ö", "€", "\U0001f600", "דּ"]
    positions = [out.index(f'"{k}":') for k in order]
    assert positions == sorted(positions)
    assert out.startswith('{"\\r":"Carriage Return","1":"One"')


def test_integers_and_nesting() -> None:
    assert canonicalize({"b": [1, 2, {"z": None, "a": True}], "a": -0.0}) == (
        b'{"a":0,"b":[1,2,{"a":true,"z":null}]}'
    )
    assert canonicalize(2**53) == b"9007199254740992"
    with pytest.raises(CanonicalizationError):
        canonicalize(2**53 + 1)  # would silently change value in an IEEE-754 consumer


@pytest.mark.parametrize(
    "bad",
    [
        {1: "non-string key"},
        {"x": b"bytes"},
        {"x": {1, 2}},
        "lone \ud800 surrogate",
        {"\udc00": 1},
        float("nan"),
    ],
)
def test_ambiguous_values_are_rejected(bad: object) -> None:
    with pytest.raises(CanonicalizationError):
        canonicalize(bad)


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1, "a": 2}',
        '{"a": NaN}',
        '{"a": Infinity}',
        '{"a": -Infinity}',
        '{"a": 1e400}',
        '{"a": "\\ud800"}',
        "{bad json",
    ],
)
def test_strict_parse_rejects_ambiguous_documents(text: str) -> None:
    with pytest.raises(CanonicalizationError):
        canonicalize(loads_strict(text))


def test_same_document_same_digest_whatever_the_formatting() -> None:
    a = loads_strict('{"b": 1.50, "a": [1e2, "x"]}')
    b = loads_strict('{\n  "a": [100, "x"],\n  "b": 1.5\n}')
    assert hash_json(a, alg="sha-256") == hash_json(b, alg="sha-256")
    assert str(hash_json(a, alg="sha-256")) == (
        "sha-256:" + hashlib.sha256(b'{"a":[100,"x"],"b":1.5}').hexdigest()
    )


def test_digest_always_carries_its_algorithm() -> None:
    d = hash_bytes(b"abc", alg="sha-256")
    assert str(d).startswith("sha-256:") and Digest.parse(str(d)) == d
    assert len(hash_bytes(b"abc", alg="sha-384").value) == 48
    assert len(hash_bytes(b"abc", alg="sha-512").value) == 64
    for bad in (
        d.value.hex(),  # no algorithm id
        "md5:" + hashlib.md5(b"abc").hexdigest(),  # noqa: S324 - not allowed, by design
        "sha-1:" + hashlib.sha1(b"abc").hexdigest(),  # noqa: S324
        "sha-256:" + d.value.hex()[:-2],  # wrong length
        "sha-256:" + d.value.hex().upper(),
        "sha-256:zz",
    ):
        with pytest.raises(DigestError):
            Digest.parse(bad)
    with pytest.raises(DigestError):
        hash_bytes(b"abc", alg="sha-1")
    assert not d.matches(hash_bytes(b"abd", alg="sha-256"))
