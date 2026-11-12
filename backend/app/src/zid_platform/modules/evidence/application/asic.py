"""ASiC containers (ETSI EN 319 162): ASiC-S and ASiC-E carrying CAdES signatures (ZID-UAT-005 DOC-01 to DOC-09; ZID-INT-002 section 09).

What is read and what is refused, kept narrow and safe:

- the ZIP is opened defensively: entry count, total size and per-entry compression ratio are capped, encrypted entries and names that escape the
  container are refused, and the `mimetype` entry must come first, stored uncompressed, and name the container type;
- ASiC-S: one data file at the root and `META-INF/signature.p7s`, a CAdES signature detached over that file;
- ASiC-E: one or more `META-INF/ASiCManifest*.xml`, each listing data files with digests, and one CAdES signature over each manifest
  (`META-INF/signature*.p7s`, paired by the number in the name). Every referenced file's digest must match, and a data file that no manifest names is
  reported as unsigned, never assumed covered;
- XAdES inside an ASiC (`META-INF/signatures.xml`) and ASiC time-stamp tokens (`META-INF/timestamp.tst`) are reported as unsupported here, not guessed at.

Cryptographic checking of each CAdES signature is `cms.inspect_cades`; this module only finds what each signature signs.
"""

from __future__ import annotations

import base64
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Final

from defusedxml import DefusedXmlException
from defusedxml import ElementTree as SafeET
from defusedxml.ElementTree import ParseError

ASIC_S: Final = "application/vnd.etsi.asic-s+zip"
ASIC_E: Final = "application/vnd.etsi.asic-e+zip"
MAX_ENTRIES = 200
MAX_TOTAL = 50 * 1024 * 1024
MAX_RATIO = 100
MAX_MANIFEST = 1024 * 1024
DIGESTS = {
    "http://www.w3.org/2001/04/xmlenc#sha256": "sha256",
    "http://www.w3.org/2001/04/xmlenc#sha512": "sha512",
    "http://www.w3.org/2001/04/xmldsig-more#sha384": "sha384",
}  # SHA-1 and unknown algorithms are not accepted


class AsicError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(slots=True)
class Container:
    kind: str  # ASIC_S | ASIC_E
    data: dict[str, bytes]
    cades: list[tuple[str, bytes, bytes]] = field(
        default_factory=list
    )  # (signature name, signature, the exact bytes it signs)
    problems: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)


def mimetype_of(content: bytes) -> str | None:
    """The container type a ZIP's first entry names, read from the local header alone (for sniffing), or None."""
    if len(content) < 38 or not content.startswith(b"PK\x03\x04"):
        return None
    method = int.from_bytes(content[8:10], "little")
    csize = (
        int.from_bytes(content[18:20], "little") | int.from_bytes(content[20:22], "little") << 16
    )
    n, extra = int.from_bytes(content[26:28], "little"), int.from_bytes(content[28:30], "little")
    if method != 0 or content[30 : 30 + n] != b"mimetype":
        return None
    body = content[30 + n + extra : 30 + n + extra + csize]
    return body.decode("ascii", "replace") if body in (ASIC_S.encode(), ASIC_E.encode()) else None


def _open(content: bytes) -> zipfile.ZipFile:
    try:
        z = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as e:
        raise AsicError("NOT_A_ZIP") from e
    infos = z.infolist()
    if len(infos) > MAX_ENTRIES:
        raise AsicError("TOO_MANY_ENTRIES")
    if sum(i.file_size for i in infos) > MAX_TOTAL:
        raise AsicError("CONTAINER_TOO_LARGE")
    for i in infos:
        if i.flag_bits & 0x1:
            raise AsicError("ENCRYPTED_ENTRY", i.filename)
        if (
            i.compress_size
            and i.file_size / max(i.compress_size, 1) > MAX_RATIO
            and i.file_size > 1024 * 1024
        ):
            raise AsicError("COMPRESSION_RATIO_TOO_HIGH", i.filename)
        name = i.filename
        if name.startswith(("/", "\\")) or ".." in name.split("/") or ":" in name.split("/")[0]:
            raise AsicError("ENTRY_NAME_ESCAPES_THE_CONTAINER", name)
    return z


def _manifest(xml: bytes) -> list[tuple[str, str, bytes]]:
    """(URI, algorithm, expected digest) per data object reference of an ASiCManifest. DOCTYPE and entities are refused outright."""
    if len(xml) > MAX_MANIFEST or re.search(rb"<!DOCTYPE|<!ENTITY", xml, re.IGNORECASE):
        raise AsicError("MANIFEST_UNSAFE")
    try:
        root = SafeET.fromstring(xml)
    except (ParseError, DefusedXmlException) as e:
        raise AsicError("MANIFEST_MALFORMED") from e
    out = []
    for ref in root.iter():
        if ref.tag.rsplit("}", 1)[-1] != "DataObjectReference":
            continue
        uri = ref.attrib.get("URI", "")
        method = next(
            (
                c.attrib.get("Algorithm", "")
                for c in ref.iter()
                if c.tag.rsplit("}", 1)[-1] == "DigestMethod"
            ),
            "",
        )
        value = next(
            (
                (c.text or "").strip()
                for c in ref.iter()
                if c.tag.rsplit("}", 1)[-1] == "DigestValue"
            ),
            "",
        )
        try:
            out.append((uri, method, base64.b64decode(value, validate=True)))
        except ValueError as e:
            raise AsicError("MANIFEST_DIGEST_NOT_BASE64", uri) from e
    return out


def read(content: bytes) -> Container:
    z = _open(content)
    names = [i.filename for i in z.infolist()]
    if not names or names[0] != "mimetype":
        raise AsicError("MIMETYPE_NOT_FIRST")
    first = z.infolist()[0]
    if first.compress_type != zipfile.ZIP_STORED:
        raise AsicError("MIMETYPE_COMPRESSED")
    kind = z.read("mimetype").decode("ascii", "replace")
    if kind not in (ASIC_S, ASIC_E):
        raise AsicError("MIMETYPE_UNKNOWN", kind)
    files = [n for n in names if not n.endswith("/") and n != "mimetype"]
    data = {n: z.read(n) for n in files if not n.startswith("META-INF/")}
    meta = [n for n in files if n.startswith("META-INF/")]
    c = Container(kind=kind, data=data)
    if any(n.startswith("META-INF/signatures") and n.endswith(".xml") for n in meta):
        c.unsupported.append("XADES_IN_ASIC_NOT_SUPPORTED")
    if any(n.startswith("META-INF/timestamp") for n in meta):
        c.unsupported.append("ASIC_TIMESTAMP_TOKEN_NOT_SUPPORTED")
    sigs = {n: z.read(n) for n in meta if re.fullmatch(r"META-INF/signature\d*\.p7s", n)}
    if kind == ASIC_S:
        if len(data) != 1:
            c.problems.append("ASIC_S_NEEDS_EXACTLY_ONE_DATA_FILE")
            return c
        only = next(iter(data.values()))
        if "META-INF/signature.p7s" in sigs:
            c.cades.append(("META-INF/signature.p7s", sigs["META-INF/signature.p7s"], only))
        elif not c.unsupported:
            c.problems.append("NO_SIGNATURE_IN_CONTAINER")
        return c
    manifests = {n: z.read(n) for n in meta if re.fullmatch(r"META-INF/ASiCManifest\d*\.xml", n)}
    if not manifests:
        c.problems.append("ASIC_E_HAS_NO_MANIFEST")
        return c
    covered: set[str] = set()
    for mname, mxml in sorted(manifests.items()):
        num = re.search(r"(\d*)\.xml$", mname).group(1)  # type: ignore[union-attr]
        sname = f"META-INF/signature{num}.p7s"
        try:
            refs = _manifest(mxml)
        except AsicError as e:
            c.problems.append(f"{e.code}:{mname}")
            continue
        for uri, algorithm, expected in refs:
            algo = DIGESTS.get(algorithm)
            if algo is None:
                c.problems.append(f"DIGEST_ALGORITHM_UNSUPPORTED:{uri}")
            elif uri not in data:
                c.problems.append(f"REFERENCED_FILE_MISSING:{uri}")
            elif hashlib.new(algo, data[uri]).digest() != expected:
                c.problems.append(f"DATA_FILE_CHANGED:{uri}")
            else:
                covered.add(uri)
        if sname in sigs:
            c.cades.append((sname, sigs[sname], mxml))
        else:
            c.problems.append(f"MANIFEST_HAS_NO_SIGNATURE:{mname}")
    c.problems += [f"DATA_FILE_NOT_SIGNED:{n}" for n in sorted(set(data) - covered)]
    return c
