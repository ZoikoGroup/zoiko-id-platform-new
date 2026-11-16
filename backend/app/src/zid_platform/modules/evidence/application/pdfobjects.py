"""A small, strict PDF object reader and the one later update a signed PDF may receive: validation data (CR-073 P2-1; PAdES-LT).

Why this exists: a PAdES long-term signature adds its certificates, CRLs and OCSP responses AFTER signing, in a `/DSS` dictionary appended as an
incremental update. Those bytes are outside the signature's byte range, so a validator that says "anything added after signing is a change"
refuses every long-term signature, and one that says "ignore what was added" accepts a document whose pages were rewritten. This module
takes the narrow path between them and fails closed:

- it reads indirect objects (dictionaries, arrays, names, numbers, strings, references and Flate or unfiltered streams), with limits on
  nesting, object count and decompressed size, and it does NOT read object streams or cross-reference streams: an update that needs them is
  "not a validation-data update";
- an update counts as validation data ONLY when every object it defines is one of: the catalog (redefined with /DSS added and every other entry
  unchanged), the /DSS dictionary, its /VRI entries, or the certificate, CRL and OCSP streams they list. One other object, one redefined
  object, one changed catalog entry, and the whole update is refused and the caller keeps reporting content added after signing;
- a DOCUMENT TIMESTAMP update is recognised only in one narrow shape: the timestamp dictionary, ONE signature field for it, the form entry
  gaining exactly that field, and the page's annotation list gaining exactly that field. The field is either invisible (zero rectangle, no
  appearance) or a VISIBLE stamp: a valid rectangle no larger than a quarter of the page, and one appearance that is a plain Form XObject stream
  (no scripts, actions or new resources; any resource it names already existed in the signed revision). A visible stamp is the one case where
  something drawn after signing could cover content, so it is bounded in size, reported to the caller (`visible_stamps`), and a visible
  field with no appearance is refused. A second kind of object, or any other change to the form or the page, and the whole update is refused;
- anything else that adds page content, fields or annotations stays "content added after signing".

Pure: bytes in, evidence out. No network, no files.
"""

from __future__ import annotations

import base64
import re
import zlib
from dataclasses import dataclass, field
from typing import Any

from cryptography import x509
from cryptography.x509 import ocsp

MAX_OBJECTS = 20_000
MAX_DEPTH = 24
MAX_STREAM = 10 * 1024 * 1024

_OBJ = re.compile(rb"(?<![0-9])(\d{1,10})\s+(\d{1,5})\s+obj\b")
_WS = b" \t\r\n\x0c\x00"
_DELIMS = b"()<>[]{}/%"
_NUMBER = re.compile(rb"[+-]?\d+(?:\.\d+)?")
_REF = re.compile(rb"(\d{1,10})\s+(\d{1,5})\s+R(?![A-Za-z0-9])")


class PdfError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Ref:
    number: int
    generation: int = 0


@dataclass(frozen=True, slots=True)
class Name:
    value: str


@dataclass(slots=True)
class Obj:
    number: int
    value: Any
    stream: bytes | None = None


def _skip(data: bytes, pos: int) -> int:
    while pos < len(data):
        if data[pos] in _WS:
            pos += 1
        elif data[pos : pos + 1] == b"%":
            while pos < len(data) and data[pos] not in b"\r\n":
                pos += 1
        else:
            break
    return pos


def _value(data: bytes, pos: int, depth: int = 0) -> tuple[Any, int]:
    if depth > MAX_DEPTH:
        raise PdfError("NESTING_TOO_DEEP")
    pos = _skip(data, pos)
    if pos >= len(data):
        raise PdfError("UNEXPECTED_END")
    c = data[pos : pos + 1]
    if data[pos : pos + 2] == b"<<":
        out: dict[str, Any] = {}
        pos += 2
        while True:
            pos = _skip(data, pos)
            if data[pos : pos + 2] == b">>":
                return out, pos + 2
            key, pos = _value(data, pos, depth + 1)
            if not isinstance(key, Name):
                raise PdfError("DICTIONARY_KEY_NOT_A_NAME")
            val, pos = _value(data, pos, depth + 1)
            out[key.value] = val
    if c == b"[":
        arr: list[Any] = []
        pos += 1
        while True:
            pos = _skip(data, pos)
            if data[pos : pos + 1] == b"]":
                return arr, pos + 1
            val, pos = _value(data, pos, depth + 1)
            arr.append(val)
    if c == b"/":
        end = pos + 1
        while end < len(data) and data[end] not in _WS and data[end] not in _DELIMS:
            end += 1
        return Name(data[pos + 1 : end].decode("latin-1")), end
    if c == b"(":
        level, end = 1, pos + 1
        while end < len(data) and level:
            ch = data[end : end + 1]
            if ch == b"\\":
                end += 1
            elif ch == b"(":
                level += 1
            elif ch == b")":
                level -= 1
            end += 1
        if level:
            raise PdfError("UNTERMINATED_STRING")
        return data[pos + 1 : end - 1], end
    if c == b"<":
        end = data.index(b">", pos)
        return bytes.fromhex(
            re.sub(rb"\s", b"", data[pos + 1 : end]).decode("ascii") or "00"
        ), end + 1
    ref = _REF.match(data, pos)
    if ref:
        return Ref(int(ref.group(1)), int(ref.group(2))), ref.end()
    num = _NUMBER.match(data, pos)
    if num:
        text = num.group(0)
        return (float(text) if b"." in text else int(text)), num.end()
    for word, val in ((b"true", True), (b"false", False), (b"null", None)):
        if data.startswith(word, pos):
            return val, pos + len(word)
    raise PdfError("TOKEN_NOT_UNDERSTOOD")


MAX_FILTERS = 4
MAX_COLUMNS = 1 << 20
_HEX_WS = b" \t\r\n\x0c\x00"


def _hex_decode(raw: bytes) -> bytes | None:
    body = raw.rstrip(_HEX_WS)
    if not body.endswith(b">"):
        return None
    digits = bytes(c for c in body[:-1] if c not in _HEX_WS)
    if len(digits) % 2:
        digits += b"0"  # the specification: a missing last digit is zero
    try:
        out = bytes.fromhex(digits.decode("ascii"))
    except (ValueError, UnicodeDecodeError):
        return None
    return out if len(out) <= MAX_STREAM else None


def _a85_decode(raw: bytes) -> bytes | None:
    body = raw.rstrip(_HEX_WS)
    if not body.endswith(b"~>"):
        return None
    try:
        out = base64.a85decode(body[:-2], ignorechars=_HEX_WS)
    except (ValueError, TypeError):
        return None
    return out if len(out) <= MAX_STREAM else None


def _flate(raw: bytes) -> bytes | None:
    d = zlib.decompressobj()
    try:
        out = d.decompress(raw, MAX_STREAM + 1)
    except zlib.error:
        return None
    return out if len(out) <= MAX_STREAM else None


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else b if pb <= pc else c


def _int(value: Any, default: int) -> int | None:
    if value is None:
        return default
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _predict(parms: Any, data: bytes) -> bytes | None:
    """Undo a Flate or LZW predictor (PDF 32000-1, 7.4.4.4). PNG predictors 10 to 15 and the TIFF predictor 2 at eight bits a component; anything
    else, and any data whose size does not fit the row length exactly, is not read (None)."""
    if parms is None:
        return data
    if not isinstance(parms, dict):
        return None
    predictor = _int(parms.get("Predictor"), 1)
    colors, bits, columns = (
        _int(parms.get("Colors"), 1),
        _int(parms.get("BitsPerComponent"), 8),
        _int(parms.get("Columns"), 1),
    )
    if predictor is None or colors is None or bits is None or columns is None:
        return None
    if predictor == 1:
        return data
    if not (1 <= colors <= 32 and bits in {1, 2, 4, 8, 16} and 1 <= columns <= MAX_COLUMNS):
        return None
    row = (columns * colors * bits + 7) // 8
    bpp = max(1, (colors * bits + 7) // 8)
    if predictor == 2:
        if bits != 8 or len(data) % row:
            return None
        out = bytearray(data)
        for r in range(0, len(out), row):
            for i in range(r + colors, r + row):
                out[i] = (out[i] + out[i - colors]) & 0xFF
        return bytes(out)
    if not 10 <= predictor <= 15 or len(data) % (row + 1):
        return None
    rows = len(data) // (row + 1)
    out = bytearray(rows * row)
    for y in range(rows):
        kind = data[y * (row + 1)]
        line = data[y * (row + 1) + 1 : (y + 1) * (row + 1)]
        base, above = y * row, (y - 1) * row
        for x in range(row):
            a = out[base + x - bpp] if x >= bpp else 0
            b = out[above + x] if y else 0
            c = out[above + x - bpp] if y and x >= bpp else 0
            if kind == 0:
                v = line[x]
            elif kind == 1:
                v = line[x] + a
            elif kind == 2:
                v = line[x] + b
            elif kind == 3:
                v = line[x] + ((a + b) >> 1)
            elif kind == 4:
                v = line[x] + _paeth(a, b, c)
            else:
                return None
            out[base + x] = v & 0xFF
    return bytes(out)


def _decode(dictionary: dict[str, Any], raw: bytes) -> bytes | None:
    """The stream's bytes after its filters, or None when they cannot be read in full. Read: no filter, and a chain of up to four of FlateDecode (with a
    PNG or TIFF predictor), ASCIIHexDecode and ASCII85Decode. Not read: any other filter (LZW, images, encryption), parameters that do not line up with the
    filters, and anything over the size limit. None is never an error to guess around: the caller keeps the object as one nothing may add."""
    flt = dictionary.get("Filter")
    if flt is None:
        return raw
    names = [flt] if isinstance(flt, Name) else flt if isinstance(flt, list) else []
    if not 1 <= len(names) <= MAX_FILTERS or not all(isinstance(n, Name) for n in names):
        return None
    parms = dictionary.get("DecodeParms")
    if parms is None:
        plist: list[Any] = [None] * len(names)
    elif isinstance(parms, dict) and len(names) == 1:
        plist = [parms]
    elif isinstance(parms, list) and len(parms) == len(names):
        plist = list(parms)
    else:
        return None  # parameters that do not line up with the filters are not guessed at
    data: bytes | None = raw
    for name, parm in zip(names, plist, strict=True):
        if data is None:
            return None
        if name.value in {"FlateDecode", "Fl"}:
            data = _flate(data)
            data = None if data is None else _predict(parm, data)
        elif parm is not None and not (isinstance(parm, dict) and not parm):
            return None  # only Flate takes parameters here
        elif name.value in {"ASCIIHexDecode", "AHx"}:
            data = _hex_decode(data)
        elif name.value in {"ASCII85Decode", "A85"}:
            data = _a85_decode(data)
        else:
            return None
    return data


def parse_objects(data: bytes) -> dict[int, Obj]:
    """Every indirect object in `data`, the LAST definition of a number winning (as in an incremental update). An object that cannot be read
    is skipped, never guessed at; the reader stops at the object limit."""
    out: dict[int, Obj] = {}
    pos = 0
    count = 0
    while count < MAX_OBJECTS:
        m = _OBJ.search(data, pos)
        if not m:
            break
        count += 1
        number = int(m.group(1))
        try:
            value, after = _value(data, m.end())
        except (PdfError, ValueError, IndexError):
            out[number] = Obj(
                number, None
            )  # defined but unreadable: it counts as an object, so an update holding one is never "only validation data"
            pos = m.end()
            continue
        stream: bytes | None = None
        probe = _skip(data, after)
        if isinstance(value, dict) and data.startswith(b"stream", probe):
            start = probe + 6
            if data[start : start + 2] == b"\r\n":
                start += 2
            elif data[start : start + 1] in (b"\n", b"\r"):
                start += 1
            length = value.get("Length")
            end: int | None = None
            if isinstance(length, int) and 0 <= length <= MAX_STREAM:
                tail = _skip(data, start + length)
                if data.startswith(b"endstream", tail):
                    end = start + length
            if end is None:
                found = data.find(b"endstream", start)
                end = found if found != -1 else None
            if end is not None:
                stream = _decode(value, data[start:end])
                after = end + 9
        out[number] = Obj(number, value, stream)
        if isinstance(value, dict):
            kind = value.get("Type")
            if kind == Name("XRef"):
                del out[number]  # a cross-reference stream only indexes objects; it holds none
            elif (
                kind == Name("ObjStm")
                and stream is not None
                and _expand_object_stream(value, stream, out)
            ):
                del out[
                    number
                ]  # fully read: its objects are in `out` instead. Any doubt and it stays, as an object nothing may add
        pos = after
    return out


def _expand_object_stream(info: dict[str, Any], data: bytes, out: dict[int, Obj]) -> bool:
    """Read the objects held in an object stream into `out` (as plain objects, never streams). True only if EVERY one was read: a stream that
    is malformed, short, over the limits or holds anything unreadable is not expanded, so the caller keeps it as an object nothing may add."""
    count, first = info.get("N"), info.get("First")
    if "Extends" in info or not (isinstance(count, int) and isinstance(first, int)):
        return False
    if not (0 <= count <= MAX_OBJECTS - len(out) and 0 <= first <= len(data)):
        return False
    try:
        header = [int(x) for x in data[:first].split()]
    except ValueError:
        return False
    if len(header) != 2 * count:
        return False
    found: dict[int, Obj] = {}
    for i in range(count):
        number, offset = header[2 * i], header[2 * i + 1]
        if number in found or offset < 0 or first + offset >= len(data):
            return False
        try:
            value, _ = _value(data, first + offset)
        except (PdfError, ValueError, IndexError):
            return False
        found[number] = Obj(number, value)
    out.update(found)
    return True


@dataclass(slots=True)
class DssEvidence:
    certificates: list[x509.Certificate] = field(default_factory=list)
    crls: list[x509.CertificateRevocationList] = field(default_factory=list)
    ocsp_responses: list[ocsp.OCSPResponse] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.certificates or self.crls or self.ocsp_responses)


def _ref_list(value: Any) -> list[Ref] | None:
    if value is None:
        return []
    if isinstance(value, Ref):
        return [value]
    if isinstance(value, list) and all(isinstance(v, Ref) for v in value):
        return list(value)
    return None


@dataclass(frozen=True, slots=True)
class DocTimeStampObject:
    number: int
    byte_range: tuple[int, int, int, int]
    contents: (
        bytes  # the timestamp token as it sits in the file (hex-decoded, possibly zero padded)
    )


@dataclass(slots=True)
class ValidationUpdate:
    evidence: DssEvidence
    has_dss: bool
    timestamps: list[DocTimeStampObject]
    visible_stamps: int = 0  # timestamp stamps that are drawn on a page (each bounded to a quarter of it, with a plain appearance)


_WIDGET_KEYS = frozenset({"Type", "Subtype", "FT", "T", "Rect", "F", "P", "V", "AP"})
_FORM_KEYS = frozenset(
    {"Type", "Subtype", "BBox", "Matrix", "Resources", "Length", "Filter", "FormType"}
)
MAX_VISIBLE_FRACTION = 0.25  # a visible timestamp stamp may cover at most this share of its page


def _merged(before: dict[int, Obj], after: dict[int, Obj]) -> dict[int, Obj]:
    return {**before, **after}


def _follow(objs: dict[int, Obj], value: Any) -> Any:
    depth = 0
    while isinstance(value, Ref) and depth < 8:
        o = objs.get(value.number)
        value = o.value if o else None
        depth += 1
    return value


def _zero_rect(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 4
        and all(isinstance(v, int | float) and v == 0 for v in value)
    )


def _numbers(value: Any, count: int) -> list[float] | None:
    if (
        isinstance(value, list)
        and len(value) == count
        and all(isinstance(v, int | float) and not isinstance(v, bool) for v in value)
    ):
        return [float(v) for v in value]
    return None


def _rect_area(value: Any) -> float | None:
    """The area of a valid, non-degenerate rectangle, or None."""
    n = _numbers(value, 4)
    if n is None:
        return None
    width, height = abs(n[2] - n[0]), abs(n[3] - n[1])
    return width * height if width > 0 and height > 0 else None


def _refs_in(value: Any) -> list[int]:
    if isinstance(value, Ref):
        return [value.number]
    if isinstance(value, dict):
        return [n for v in value.values() for n in _refs_in(v)]
    if isinstance(value, list):
        return [n for v in value for n in _refs_in(v)]
    return []


def _plain_form(obj: Obj | None, before: dict[int, Obj]) -> bool:
    """An appearance that is only drawing: a Form XObject stream whose dictionary holds nothing but the keys of a plain form, whose bounding
    box is valid, and whose resources name only objects the signed revision already had."""
    if obj is None or obj.stream is None or not isinstance(obj.value, dict):
        return False
    v = obj.value
    if set(v) - _FORM_KEYS or v.get("Subtype") != Name("Form") or _rect_area(v.get("BBox")) is None:
        return False
    if v.get("Type") not in (None, Name("XObject")):
        return False
    res = v.get("Resources")
    if res is not None and not isinstance(res, dict):
        return False
    return all(n in before for n in _refs_in(res))


def _timestamps_and_widgets(
    after: dict[int, Obj], before: dict[int, Obj]
) -> tuple[list[DocTimeStampObject], dict[int, int], dict[int, float], set[int]] | None:
    """The document timestamp dictionaries in the update, and for each the number of its widget. None when anything about them is not exactly
    the narrow shape: one signature field per timestamp (invisible, or a bounded visible stamp with a plain appearance), nothing else. Also returns
    the visible widgets' rectangle areas and the appearance objects the update defines."""
    stamps: list[DocTimeStampObject] = []
    for n, o in after.items():
        v = o.value
        if not (isinstance(v, dict) and v.get("Type") == Name("DocTimeStamp")):
            continue
        br = v.get("ByteRange")
        contents = v.get("Contents")
        if (
            n in before
            or v.get("SubFilter") != Name("ETSI.RFC3161")
            or not (
                isinstance(br, list)
                and len(br) == 4
                and all(isinstance(x, int) and x >= 0 for x in br)
            )
            or not isinstance(contents, bytes)
        ):
            return None
        stamps.append(DocTimeStampObject(n, (br[0], br[1], br[2], br[3]), contents))
    widgets: dict[int, int] = {}  # timestamp object number -> widget object number
    visible: dict[int, float] = {}  # widget object number -> rectangle area, for the visible ones
    appearances: set[int] = set()
    for n, o in after.items():
        v = o.value
        if not (
            isinstance(v, dict)
            and v.get("Subtype") == Name("Widget")
            and v.get("FT") == Name("Sig")
        ):
            continue
        link = v.get("V")
        if (
            n in before
            or set(v) - _WIDGET_KEYS
            or not isinstance(link, Ref)
            or not isinstance(v.get("P"), Ref)
            or link.number in widgets
        ):
            return None
        if _zero_rect(v.get("Rect")):
            if "AP" in v:
                return None  # an invisible field carries no appearance
        else:
            area = _rect_area(v.get("Rect"))
            ap = v.get("AP")
            normal = ap.get("N") if isinstance(ap, dict) and set(ap) == {"N"} else None
            if (
                area is None
                or not isinstance(normal, Ref)
                or normal.number not in after
                or normal.number in before
                or not _plain_form(after[normal.number], before)
            ):
                return None  # a visible stamp with no readable plain appearance is not recognised
            visible[n] = area
            appearances.add(normal.number)
        widgets[link.number] = n
    if {s.number for s in stamps} != set(widgets):
        return None  # a timestamp with no field, or a signature field that is not one of the timestamps
    if len(appearances) != len(visible):
        return None  # two stamps may not share one appearance
    return stamps, widgets, visible, appearances


def validation_update(signed: bytes, appended: bytes) -> ValidationUpdate | None:
    """What `appended` adds to the signed revision IF AND ONLY IF all of it is validation data: a /DSS (certificates, CRLs, OCSP) and/or document
    timestamps (see the module note); otherwise None. `signed` is the revision the signature covers; `appended` is every byte after it, which may
    hold several updates (the last definition of an object wins, as in the file itself)."""
    after = parse_objects(appended)
    if not after:
        return None
    before = parse_objects(signed)
    both = _merged(before, after)
    allowed: set[int] = set()
    redefinable: set[int] = (
        set()
    )  # objects of the signed revision this kind of update may legitimately redefine

    catalog_no = next(
        (
            n
            for n, o in after.items()
            if isinstance(o.value, dict) and o.value.get("Type") == Name("Catalog")
        ),
        None,
    )
    old_cat: dict[str, Any] | None = None
    new_cat: dict[str, Any] | None = None
    if catalog_no is not None:
        if catalog_no not in before:
            return None
        old_cat, new_cat = before[catalog_no].value, after[catalog_no].value
        if not isinstance(old_cat, dict) or old_cat.get("Type") != Name("Catalog"):
            return None
        drop = {"DSS", "Extensions", "AcroForm"}
        if {k: v for k, v in old_cat.items() if k not in drop} != {
            k: v for k, v in new_cat.items() if k not in drop
        }:
            return None  # the catalog changed in some other way
        allowed.add(catalog_no)
        redefinable.add(catalog_no)
    else:
        # no catalog in the update: find the one of the signed revision, for the form entry
        old_cat = next(
            (
                o.value
                for o in before.values()
                if isinstance(o.value, dict) and o.value.get("Type") == Name("Catalog")
            ),
            None,
        )

    # ---- the /DSS, if the catalog carries one
    evidence = DssEvidence()
    has_dss = bool(new_cat and "DSS" in new_cat)
    streams: list[Ref] = []
    dss_value: Any = None
    if has_dss and new_cat is not None:
        dss_value = new_cat["DSS"]
        if isinstance(dss_value, Ref):
            if dss_value.number not in after:
                return None
            allowed.add(dss_value.number)
            dss_value = after[dss_value.number].value
        if not isinstance(dss_value, dict):
            return None
        for key in ("Certs", "CRLs", "OCSPs"):
            refs = _ref_list(dss_value.get(key))
            if refs is None:
                return None
            streams.extend(refs)
        vri = dss_value.get("VRI")
        if isinstance(vri, Ref):
            if vri.number not in after:
                return None
            allowed.add(vri.number)
            vri = after[vri.number].value
        if vri is not None:
            if not isinstance(vri, dict):
                return None
            for entry in vri.values():
                if isinstance(entry, Ref):
                    if entry.number not in after:
                        return None
                    allowed.add(entry.number)
                    entry = after[entry.number].value
                if not isinstance(entry, dict):
                    return None
                for key in ("Cert", "CRL", "OCSP"):
                    refs = _ref_list(entry.get(key))
                    if refs is None:
                        return None
                    streams.extend(refs)
        for ref in streams:
            obj = after.get(ref.number) or before.get(ref.number)
            if obj is None or obj.stream is None:
                return None
            if ref.number in after:
                allowed.add(ref.number)

    # ---- document timestamps, each with its one invisible field, the form entry and the page annotation list
    found = _timestamps_and_widgets(after, before)
    if found is None:
        return None
    stamps, widgets, visible, appearances = found
    if stamps:
        allowed |= {s.number for s in stamps} | set(widgets.values()) | appearances
        new_fields = set(widgets.values())
        # the form: the catalog's AcroForm, as it stands after the update, may differ from before ONLY by these fields
        old_ac_value = old_cat.get("AcroForm") if old_cat else None
        new_ac_value = (
            new_cat.get("AcroForm")
            if new_cat is not None and "AcroForm" in new_cat
            else old_ac_value
        )
        if new_ac_value is None:
            return None
        for candidate in (old_ac_value, new_ac_value):
            if isinstance(candidate, Ref) and candidate.number in before:
                redefinable.add(candidate.number)
        if isinstance(new_ac_value, Ref):
            if new_ac_value.number not in after and not (
                isinstance(old_ac_value, Ref) and new_ac_value == old_ac_value
            ):
                return None
            if new_ac_value.number in after:
                allowed.add(new_ac_value.number)
        old_ac = _follow(before, old_ac_value) if old_ac_value is not None else None
        new_ac = _follow(both, new_ac_value)
        if not isinstance(new_ac, dict) or (old_ac is not None and not isinstance(old_ac, dict)):
            return None
        old_fields = _follow(before, old_ac.get("Fields")) if old_ac else []
        new_fields_list = _follow(both, new_ac.get("Fields"))
        if not (isinstance(old_fields, list) and isinstance(new_fields_list, list)):
            return None
        if new_fields_list[: len(old_fields)] != old_fields:
            return None
        if {
            r.number for r in new_fields_list[len(old_fields) :] if isinstance(r, Ref)
        } != new_fields or len(new_fields_list) - len(old_fields) != len(new_fields):
            return None
        if old_ac is None:
            if set(new_ac) - {"Fields", "SigFlags"}:
                return None
        elif {k: v for k, v in old_ac.items() if k not in {"Fields", "SigFlags"}} != {
            k: v for k, v in new_ac.items() if k not in {"Fields", "SigFlags"}
        }:
            return None
        # the pages: each page that holds a widget gets those widgets appended to its annotation list and nothing else
        by_page: dict[int, set[int]] = {}
        for w in widgets.values():
            page_ref = after[w].value["P"]
            by_page.setdefault(page_ref.number, set()).add(w)
        for page_no, wset in by_page.items():
            if page_no not in before or page_no not in after:
                return None
            old_page, new_page = before[page_no].value, after[page_no].value
            if not (isinstance(old_page, dict) and isinstance(new_page, dict)) or old_page.get(
                "Type"
            ) != Name("Page"):
                return None
            if {k: v for k, v in old_page.items() if k != "Annots"} != {
                k: v for k, v in new_page.items() if k != "Annots"
            }:
                return None
            old_an = _follow(before, old_page.get("Annots")) if "Annots" in old_page else []
            new_an = _follow(both, new_page.get("Annots"))
            if (
                not (isinstance(old_an, list) and isinstance(new_an, list))
                or new_an[: len(old_an)] != old_an
            ):
                return None
            added = new_an[len(old_an) :]
            if {r.number for r in added if isinstance(r, Ref)} != wset or len(added) != len(wset):
                return None
            for w in wset & set(visible):
                box = _numbers(old_page.get("MediaBox"), 4)
                page_area = _rect_area(box)
                if page_area is None or visible[w] > MAX_VISIBLE_FRACTION * page_area:
                    return None  # a stamp that could cover much of the page, or a page whose size is not known
            allowed.add(page_no)
            redefinable.add(page_no)
            for annots in (old_page.get("Annots"), new_page.get("Annots")):
                if isinstance(annots, Ref) and annots.number in before:
                    redefinable.add(annots.number)
                    if annots.number in after:
                        allowed.add(annots.number)

    if not has_dss and not stamps:
        return None  # nothing here is recognised as validation data
    if set(after) - allowed:
        return None  # something else was added or redefined: this is not only validation data
    if any(n in before and n not in redefinable for n in after):
        return None

    if has_dss and isinstance(dss_value, dict):
        for key, kind in (("Certs", "cert"), ("CRLs", "crl"), ("OCSPs", "ocsp")):
            for ref in _ref_list(dss_value.get(key)) or []:
                obj = after.get(ref.number) or before.get(ref.number)
                if obj is None or obj.stream is None:
                    continue
                try:
                    if kind == "cert":
                        evidence.certificates.append(x509.load_der_x509_certificate(obj.stream))
                    elif kind == "crl":
                        evidence.crls.append(x509.load_der_x509_crl(obj.stream))
                    else:
                        evidence.ocsp_responses.append(ocsp.load_der_ocsp_response(obj.stream))
                except ValueError:
                    continue  # an item that does not parse is skipped, never trusted
    return ValidationUpdate(
        evidence, has_dss, sorted(stamps, key=lambda s: s.byte_range[2]), len(visible)
    )


def dss_update(signed: bytes, appended: bytes) -> DssEvidence | None:
    """The validation data in a /DSS update, when the update is only validation data and carries a /DSS; otherwise None."""
    update = validation_update(signed, appended)
    return update.evidence if update is not None and update.has_dss else None


# ------------------------------------------------------------------------------------------------ AcroForm fields

MAX_FIELDS = 500
MAX_VALUE = 500


@dataclass(frozen=True, slots=True)
class FormField:
    name: str  # fully qualified: parent.child
    field_type: str  # Tx, Btn, Ch (signature fields are not listed)
    value: str | None


def _text(raw: Any) -> str | None:
    if isinstance(raw, Name):
        return raw.value[:MAX_VALUE]
    if not isinstance(raw, bytes):
        return None
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", errors="replace")[:MAX_VALUE]
    return raw.decode("latin-1")[:MAX_VALUE]  # PDFDocEncoding agrees with Latin-1 for ordinary text


def form_fields(pdf: bytes) -> list[FormField]:
    """The AcroForm fields of the FINAL revision of a PDF (the last definition of each object), fully qualified, with their values. Signature
    fields are skipped. Nothing about who filled them in or whether they were signed is known here: that is the signature check's job."""
    objs = parse_objects(pdf)

    def resolve(v: Any) -> Any:
        depth = 0
        while isinstance(v, Ref) and depth < 8:
            o = objs.get(v.number)
            v = o.value if o else None
            depth += 1
        return v

    catalog = next(
        (
            o.value
            for o in reversed(list(objs.values()))
            if isinstance(o.value, dict) and o.value.get("Type") == Name("Catalog")
        ),
        None,
    )
    if not isinstance(catalog, dict):
        return []
    acro = resolve(catalog.get("AcroForm"))
    if not isinstance(acro, dict):
        return []
    out: list[FormField] = []

    def walk(node: Any, prefix: str, inherited_type: str | None, depth: int) -> None:
        if depth > 12 or len(out) >= MAX_FIELDS:
            return
        d = resolve(node)
        if not isinstance(d, dict):
            return
        partial = _text(d.get("T")) or ""
        name = f"{prefix}.{partial}" if prefix and partial else (partial or prefix)
        ftype = d.get("FT")
        ftype_s = ftype.value if isinstance(ftype, Name) else inherited_type
        kids = resolve(d.get("Kids"))
        if isinstance(kids, list) and kids:
            for kid in kids:
                walk(kid, name, ftype_s, depth + 1)
            return
        if ftype_s in {None, "Sig"} or not name:
            return
        out.append(FormField(name, ftype_s, _text(d.get("V"))))

    fields = resolve(acro.get("Fields"))
    if isinstance(fields, list):
        for node in fields:
            walk(node, "", None, 0)
    return out
