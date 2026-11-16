"""PDF stream filters the reader now understands: Flate predictors, and chains of ASCIIHex, ASCII85 and Flate (the DTV-04 follow-up).

The encoders below are written independently of the decoder, straight from PDF 32000-1 section 7.4.4.4 (PNG predictors and the TIFF predictor), so
the decoder is checked against the specification and not against itself. The rule that matters is unchanged: whatever cannot be read in full is not
read (None), and the caller then keeps the object as one nothing may add.
"""

from __future__ import annotations

import base64
import random
import zlib

import pytest
from zid_platform.modules.evidence.application import pdfobjects as pdf
from zid_platform.modules.evidence.application.pdfobjects import Name, _decode, parse_objects


def paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def png_encode(rows: list[bytes], kinds: list[int], bpp: int) -> bytes:
    """Each row prefixed with its filter type; the filtered bytes are the difference from the prediction."""
    out = bytearray()
    prev = bytes(len(rows[0]))
    for kind, row in zip(kinds, rows, strict=True):
        out.append(kind)
        for x, v in enumerate(row):
            a = row[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            predicted = {0: 0, 1: a, 2: b, 3: (a + b) // 2, 4: paeth(a, b, c)}[kind]
            out.append((v - predicted) & 0xFF)
        prev = row
    return bytes(out)


def tiff_encode(rows: list[bytes], colors: int) -> bytes:
    out = bytearray()
    for row in rows:
        for x, v in enumerate(row):
            out.append((v - (row[x - colors] if x >= colors else 0)) & 0xFF)
    return bytes(out)


def sample_rows(n: int, width: int, seed: int = 7) -> list[bytes]:
    rnd = random.Random(seed)
    return [bytes(rnd.randrange(256) for _ in range(width)) for _ in range(n)]


def stream(flt, parms=None) -> dict:
    d: dict = {"Filter": flt}
    if parms is not None:
        d["DecodeParms"] = parms
    return d


FLATE = Name("FlateDecode")


@pytest.mark.parametrize("kind", [0, 1, 2, 3, 4])
@pytest.mark.parametrize(("colors", "columns"), [(1, 9), (3, 5), (2, 7)])
def test_every_png_filter_type_is_undone_exactly(kind: int, colors: int, columns: int) -> None:
    width = columns * colors
    rows = sample_rows(6, width)
    encoded = png_encode(rows, [kind] * 6, colors)
    parms = {"Predictor": 15, "Columns": columns, "Colors": colors}
    assert _decode(stream(FLATE, parms), zlib.compress(encoded)) == b"".join(rows)


def test_png_rows_may_each_use_a_different_filter_as_a_real_writer_does() -> None:
    rows = sample_rows(10, 12, seed=11)
    kinds = [0, 1, 2, 3, 4, 4, 3, 2, 1, 0]
    encoded = png_encode(rows, kinds, 1)
    assert _decode(
        stream(FLATE, {"Predictor": 12, "Columns": 12}), zlib.compress(encoded)
    ) == b"".join(rows)


@pytest.mark.parametrize("colors", [1, 3])
def test_the_tiff_predictor_is_undone_at_eight_bits(colors: int) -> None:
    rows = sample_rows(5, 4 * colors)
    encoded = tiff_encode(rows, colors)
    parms = {"Predictor": 2, "Columns": 4, "Colors": colors}
    assert _decode(stream(FLATE, parms), zlib.compress(encoded)) == b"".join(rows)


def test_a_predictor_of_one_or_none_changes_nothing() -> None:
    raw = zlib.compress(b"plain bytes")
    assert _decode(stream(FLATE, {"Predictor": 1}), raw) == b"plain bytes"
    assert _decode(stream(FLATE, {}), raw) == b"plain bytes"
    assert _decode(stream(FLATE), raw) == b"plain bytes"
    assert _decode({}, b"no filter") == b"no filter"


@pytest.mark.parametrize(
    "parms",
    [
        {"Predictor": 13},  # columns default to 1, so the row length no longer matches
        {"Predictor": 99, "Columns": 4},
        {
            "Predictor": 2,
            "Columns": 4,
            "BitsPerComponent": 4,
        },  # the TIFF predictor is read at eight bits only
        {"Predictor": 15, "Columns": 0},
        {"Predictor": 15, "Columns": (1 << 20) + 1},
        {"Predictor": 15, "Columns": 4, "Colors": 40},
        {"Predictor": 15, "Columns": 4, "BitsPerComponent": 3},
        {"Predictor": "15", "Columns": 4},
        {"Predictor": 15, "Columns": 4.5},
        {"Predictor": True, "Columns": 4},
    ],
)
def test_parameters_that_do_not_fit_are_not_read(parms) -> None:
    rows = sample_rows(3, 4)
    assert _decode(stream(FLATE, parms), zlib.compress(png_encode(rows, [1, 1, 1], 1))) is None


def test_a_row_with_an_unknown_png_filter_type_is_not_read() -> None:
    data = bytes([9]) + bytes(4)  # filter type 9 does not exist
    assert _decode(stream(FLATE, {"Predictor": 15, "Columns": 4}), zlib.compress(data)) is None


def test_data_that_is_not_a_whole_number_of_rows_is_not_read() -> None:
    encoded = png_encode(sample_rows(3, 4), [1, 1, 1], 1)
    assert (
        _decode(stream(FLATE, {"Predictor": 15, "Columns": 4}), zlib.compress(encoded[:-1])) is None
    )


def test_a_chain_of_ascii_hex_then_flate_is_read_in_order() -> None:
    payload = b"<< /Type /Page >> and some more bytes to make it long enough to matter"
    hexed = zlib.compress(payload).hex().upper().encode() + b">"
    assert _decode(stream([Name("ASCIIHexDecode"), FLATE]), hexed) == payload
    body = hexed[:-1]
    spaced = b" ".join(body[i : i + 8] for i in range(0, len(body), 8)) + b">"
    assert _decode(stream([Name("AHx"), FLATE]), spaced) == payload


def test_a_chain_of_ascii85_then_flate_is_read_in_order() -> None:
    payload = b"the quick brown fox jumps over the lazy dog " * 4
    a85 = base64.a85encode(zlib.compress(payload)) + b"~>"
    assert _decode(stream([Name("ASCII85Decode"), FLATE]), a85) == payload


def test_a_predictor_inside_a_chain_applies_to_the_flate_step_only() -> None:
    rows = sample_rows(4, 6)
    inner = zlib.compress(png_encode(rows, [2, 2, 2, 2], 1))
    outer = inner.hex().encode() + b">"
    parms = [None, {"Predictor": 12, "Columns": 6}]
    assert _decode(stream([Name("ASCIIHexDecode"), FLATE], parms), outer) == b"".join(rows)


@pytest.mark.parametrize(
    ("flt", "parms"),
    [
        (Name("LZWDecode"), None),
        (Name("DCTDecode"), None),
        (Name("Crypt"), None),
        ([FLATE, Name("LZWDecode")], None),
        ([FLATE] * 5, None),  # longer than the longest chain read
        (
            [FLATE, FLATE],
            {"Predictor": 12, "Columns": 4},
        ),  # one parameter dictionary for two filters is ambiguous
        ([FLATE, FLATE], [None]),  # a list that does not line up
        (
            [Name("ASCIIHexDecode")],
            [{"Predictor": 12, "Columns": 4}],
        ),  # only Flate takes parameters
        ("FlateDecode", None),  # a string, not a name
        ([], None),
    ],
)
def test_an_unfamiliar_or_misaligned_chain_is_not_read(flt, parms) -> None:
    assert _decode(stream(flt, parms), zlib.compress(b"x" * 40)) is None


@pytest.mark.parametrize("raw", [b"ZZ>", b"41 42 43", b"4142>x", b""])
def test_malformed_ascii_hex_is_not_read(raw) -> None:
    assert _decode(stream([Name("ASCIIHexDecode")]), raw) is None


@pytest.mark.parametrize("raw", [b'87cURD]i,"Ebo80', b"!!!!!zzz", b"~"])
def test_malformed_ascii85_is_not_read(raw) -> None:
    assert _decode(stream([Name("ASCII85Decode")]), raw) is None


def test_a_decompression_bomb_is_not_read() -> None:
    bomb = zlib.compress(b"\x00" * (pdf.MAX_STREAM + 10))
    assert _decode(stream(FLATE), bomb) is None
    assert _decode(stream([Name("ASCIIHexDecode"), FLATE]), bomb.hex().encode() + b">") is None


def test_a_corrupt_flate_stream_is_not_read() -> None:
    assert _decode(stream(FLATE), b"not flate at all") is None
    assert _decode(stream(FLATE, {"Predictor": 12, "Columns": 4}), zlib.compress(b"x")[:-3]) is None


def object_stream_pdf(
    members: list[tuple[int, bytes]], flt: str, body_encoder, parms: str = ""
) -> bytes:
    """A tiny PDF holding one object stream (number 40) whose bytes are encoded by `body_encoder`."""
    header = b" ".join(
        f"{n} {i}".encode() for (n, _), i in zip(members, _offsets(members), strict=True)
    )
    first = len(header) + 1
    body = header + b"\n" + b" ".join(v for _, v in members)
    encoded = body_encoder(body)
    return (
        b"%PDF-1.5\n40 0 obj\n<< /Type /ObjStm /N "
        + str(len(members)).encode()
        + b" /First "
        + str(first).encode()
        + b" /Filter "
        + flt.encode()
        + (b" /DecodeParms " + parms.encode() if parms else b"")
        + b" /Length "
        + str(len(encoded)).encode()
        + b" >>\nstream\n"
        + encoded
        + b"\nendstream\nendobj\n"
    )


def _offsets(members: list[tuple[int, bytes]]) -> list[int]:
    out, pos = [], 0
    for _, v in members:
        out.append(pos)
        pos += len(v) + 1
    return out


MEMBERS = [(3, b"<< /Type /Page /Parent 4 0 R >>"), (7, b"<< /Fields [] >>")]


def test_an_object_stream_with_a_png_predictor_is_opened_and_its_objects_seen() -> None:
    def encode(body: bytes) -> bytes:
        columns = 16
        padded = body + bytes(-len(body) % columns)
        rows = [padded[i : i + columns] for i in range(0, len(padded), columns)]
        return zlib.compress(png_encode(rows, [2] * len(rows), 1))

    pdf_bytes = object_stream_pdf(
        MEMBERS, "/FlateDecode", encode, "<< /Predictor 12 /Columns 16 >>"
    )
    objs = parse_objects(pdf_bytes)
    assert set(objs) == {3, 7}, (
        "the objects inside are seen, and the container is not an object of its own"
    )
    assert objs[3].value["Type"] == Name("Page")


def test_an_object_stream_in_an_ascii_hex_and_flate_chain_is_opened() -> None:
    def encode(body: bytes) -> bytes:
        return zlib.compress(body).hex().encode() + b">"

    pdf_bytes = object_stream_pdf(MEMBERS, "[/ASCIIHexDecode /FlateDecode]", encode)
    assert set(parse_objects(pdf_bytes)) == {3, 7}


def test_an_object_stream_with_an_unreadable_filter_stays_as_an_object_nothing_may_add() -> None:
    pdf_bytes = object_stream_pdf(MEMBERS, "/LZWDecode", lambda body: body)
    objs = parse_objects(pdf_bytes)
    assert set(objs) == {40}, (
        "not opened, so the container stays and hidden content cannot ride in unread"
    )


def test_an_object_stream_whose_predictor_data_is_damaged_is_not_expanded() -> None:
    def encode(body: bytes) -> bytes:
        return zlib.compress(b"\x09" + body)  # an unknown PNG filter type at the first row

    pdf_bytes = object_stream_pdf(
        MEMBERS, "/FlateDecode", encode, "<< /Predictor 12 /Columns 16 >>"
    )
    assert set(parse_objects(pdf_bytes)) == {40}
