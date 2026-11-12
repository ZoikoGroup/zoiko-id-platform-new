"""Parsers for the official sanctions list files (ZID-ED-04 section 05). Each returns normalized `Entry` rows.

What each parser rests on, so nobody mistakes a guess for a specification:
- OFAC SDN CSV (SDN.CSV, ALT.CSV, ADD.CSV): the published fixed column order, `-0-` for an empty value, an individual typed
  `individual`, an entity typed `-0-`; birth years, nationalities and document numbers are read from the remarks text.
  CONFIRMED against the published data specification.
- UK Sanctions List CSV: columns are found by the published field headings (Unique ID, Name 1 to Name 6, Name type, D.O.B,
  Nationality(/ies), National Identifier number, Passport number, Business registration number (s)), so a reordered file
  still parses. The kind of designation is inferred (a ship column, personal name parts, a date of birth): an inference.
- UN consolidated XML: the root CONSOLIDATED_LIST with INDIVIDUALS and ENTITIES; DATAID, FIRST_NAME to FOURTH_NAME,
  REFERENCE_NUMBER, NATIONALITY/VALUE, INDIVIDUAL_ALIAS/ALIAS_NAME, INDIVIDUAL_DATE_OF_BIRTH, INDIVIDUAL_ADDRESS/COUNTRY.
  CONFIRMED for individuals against the live file; the ENTITY and INDIVIDUAL_DOCUMENT shapes are NOT confirmed.
- EU consolidated XML (sanctionEntity, nameAlias, birthdate, citizenship, identification): written from the published
  description of the financial sanctions file and NOT confirmed against a file, which needs a registered account.
Any file that does not look like its list is refused outright (`LIST_FORMAT_INVALID`), never half-loaded.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable
from functools import partial
from typing import Any

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException

from zid_platform.modules.screening.domain.lists import Entry, ListId, PartyKind
from zid_platform.modules.screening.domain.matching import normalize_identifier

YEAR = re.compile(r"\b(1[89]\d\d|20\d\d)\b")
NULL = "-0-"


class ListFormatError(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(f"LIST_FORMAT_INVALID: {detail}")
        self.code = "LIST_FORMAT_INVALID"
        self.detail = detail


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ListFormatError("undecodable")


def _clean(value: str | None) -> str:
    v = (value or "").strip()
    return "" if v == NULL else v


def _years(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(YEAR.findall(text)))


def _identifiers(*values: str) -> tuple[str, ...]:
    out: list[str] = []
    for v in values:
        for part in re.split(r"[;,]", v):
            n = normalize_identifier(part)
            if len(n) >= 5:
                out.append(n)
    return tuple(dict.fromkeys(out))


# ---- OFAC ------------------------------------------------------------------------------------------------------------

_OFAC_ID = re.compile(
    r"(?:Passport|National ID No\.|Registration ID|Tax ID No\.|Registration Number|Identification Number|"
    r"Company Number|Business Registration Number|Business Registration Document #|SSN|Cedula No\.|"
    r"Commercial Registry Number|Identity Card No\.)\s+([A-Za-z0-9][A-Za-z0-9 \-/.]{3,}?)(?=\s*\(|;|,|$)"
)
_OFAC_DOB = re.compile(r"\bDOB\s+([^;]+)")
_OFAC_NATIONALITY = re.compile(r"\b(?:nationality|citizen)\s+([A-Za-z][A-Za-z ,'-]+?)(?:;|$)")


def parse_ofac(
    sdn: bytes,
    alt: bytes | None = None,
    add: bytes | None = None,
    *,
    list_id: ListId = ListId.OFAC_SDN,
) -> list[Entry]:
    rows = list(csv.reader(io.StringIO(_decode(sdn))))
    if not rows or any(len(r) < 4 for r in rows if r):
        raise ListFormatError("SDN.CSV rows have fewer than four columns")
    aliases: dict[str, list[str]] = {}
    countries: dict[str, list[str]] = {}
    if alt:
        for r in csv.reader(io.StringIO(_decode(alt))):
            if len(r) >= 4 and _clean(r[3]):
                aliases.setdefault(r[0].strip(), []).append(_clean(r[3]))
    if add:
        for r in csv.reader(io.StringIO(_decode(add))):
            if len(r) >= 5 and _clean(r[4]):
                countries.setdefault(r[0].strip(), []).append(_clean(r[4]).casefold())
    out: list[Entry] = []
    for r in rows:
        if not r or not r[0].strip():
            continue
        ent = r[0].strip()
        kind_text = _clean(r[2]).casefold()
        kind = {
            "individual": PartyKind.PERSON,
            "vessel": PartyKind.OTHER,
            "aircraft": PartyKind.OTHER,
        }.get(kind_text, PartyKind.ENTITY)
        remarks = _clean(r[11]) if len(r) > 11 else ""
        nationality = [m.group(1).strip().casefold() for m in _OFAC_NATIONALITY.finditer(remarks)]
        out.append(
            Entry(
                list_id=list_id,
                source_ref=ent,
                kind=kind,
                primary_name=_clean(r[1]),
                aliases=tuple(dict.fromkeys(aliases.get(ent, []))),
                birth=_years(" ".join(m.group(1) for m in _OFAC_DOB.finditer(remarks))),
                countries=tuple(dict.fromkeys([*countries.get(ent, []), *nationality])),
                identifiers=_identifiers(*(m.group(1) for m in _OFAC_ID.finditer(remarks))),
                programs=tuple(
                    p.strip()
                    for p in _clean(r[3]).replace("] [", ";").strip("[]").split(";")
                    if p.strip()
                ),
            )
        )
    if not out:
        raise ListFormatError("no designations found")
    return out


# ---- UK --------------------------------------------------------------------------------------------------------------


def _h(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def parse_uk_csv(raw: bytes) -> list[Entry]:
    lines = list(csv.reader(io.StringIO(_decode(raw))))
    header_at = next((i for i, r in enumerate(lines) if "uniqueid" in {_h(c) for c in r}), None)
    if header_at is None:
        raise ListFormatError("no Unique ID heading")
    index = {_h(c): i for i, c in enumerate(lines[header_at])}

    def cell(row: list[str], *names: str) -> str:
        for n in names:
            i = index.get(_h(n))
            if i is not None and i < len(row) and row[i].strip():
                return row[i].strip()
        return ""

    groups: dict[str, list[list[str]]] = {}
    for row in lines[header_at + 1 :]:
        uid = cell(row, "Unique ID")
        if uid:
            groups.setdefault(uid, []).append(row)
    out: list[Entry] = []
    for uid, rows in groups.items():

        def full(row: list[str]) -> str:
            return " ".join(p for p in (cell(row, f"Name {n}") for n in range(1, 7)) if p)

        primary_rows = [
            r
            for r in rows
            if cell(r, "Name type").casefold().startswith("primary name")
            and "variation" not in cell(r, "Name type").casefold()
        ]
        primary = primary_rows[0] if primary_rows else rows[0]
        names = [full(r) for r in rows if r is not primary and full(r)]
        personal = any(cell(r, *[f"Name {n}" for n in range(1, 6)]) for r in rows)
        ship = any(cell(r, "Type of ship", "Ship type") for r in rows)
        dobs = " ".join(cell(r, "D.O.B", "DOB") for r in rows)
        kind = (
            PartyKind.OTHER
            if ship
            else PartyKind.PERSON
            if personal or _years(dobs)
            else PartyKind.ENTITY
        )
        out.append(
            Entry(
                list_id=ListId.UK_SANCTIONS,
                source_ref=uid,
                kind=kind,
                primary_name=full(primary),
                aliases=tuple(dict.fromkeys(n for n in names if n != full(primary))),
                birth=_years(dobs),
                countries=tuple(
                    dict.fromkeys(
                        c.strip().casefold()
                        for r in rows
                        for c in re.split(r"[;,]", cell(r, "Nationality(/ies)", "Nationality"))
                        if c.strip()
                    )
                ),
                identifiers=_identifiers(
                    *(cell(r, "National Identifier number") for r in rows),
                    *(cell(r, "Passport number") for r in rows),
                    *(
                        cell(r, "Business registration number (s)", "Business registration number")
                        for r in rows
                    ),
                ),
                programs=tuple(
                    dict.fromkeys(cell(r, "Regime Name") for r in rows if cell(r, "Regime Name"))
                ),
            )
        )
    if not out:
        raise ListFormatError("no designations found")
    return out


# ---- UN --------------------------------------------------------------------------------------------------------------


def _t(node: Any, tag: str) -> str:
    child = node.find(tag)
    return (child.text or "").strip() if child is not None and child.text else ""


def _all(node: Any, tag: str, sub: str) -> list[str]:
    out = []
    for child in node.findall(tag):
        v = _t(child, sub)
        if v:
            out.append(v)
    return out


def parse_un_xml(raw: bytes) -> list[Entry]:
    try:
        root = ET.fromstring(raw)
    except (ET.ParseError, DefusedXmlException) as e:
        raise ListFormatError("not well-formed XML, or it uses entities") from e
    if root.tag != "CONSOLIDATED_LIST":
        raise ListFormatError("root is not CONSOLIDATED_LIST")
    out: list[Entry] = []
    for container, tag, kind in (
        ("INDIVIDUALS", "INDIVIDUAL", PartyKind.PERSON),
        ("ENTITIES", "ENTITY", PartyKind.ENTITY),
    ):
        for node in root.findall(f"{container}/{tag}"):
            ref = _t(node, "DATAID")
            name = " ".join(
                p
                for p in (_t(node, f"{n}_NAME") for n in ("FIRST", "SECOND", "THIRD", "FOURTH"))
                if p
            )
            if not ref or not name:
                continue
            alias_tag = "INDIVIDUAL_ALIAS" if kind is PartyKind.PERSON else "ENTITY_ALIAS"
            dob_text = " ".join(
                " ".join(
                    filter(
                        None, (_t(d, "YEAR"), _t(d, "DATE"), _t(d, "FROM_YEAR"), _t(d, "TO_YEAR"))
                    )
                )
                for d in node.findall("INDIVIDUAL_DATE_OF_BIRTH")
            )
            addr_tag = "INDIVIDUAL_ADDRESS" if kind is PartyKind.PERSON else "ENTITY_ADDRESS"
            out.append(
                Entry(
                    list_id=ListId.UN_SC,
                    source_ref=ref,
                    kind=kind,
                    primary_name=name,
                    aliases=tuple(
                        dict.fromkeys(a for a in _all(node, alias_tag, "ALIAS_NAME") if a != name)
                    ),
                    birth=_years(dob_text),
                    countries=tuple(
                        dict.fromkeys(
                            c.casefold()
                            for c in [
                                *_all(node, "NATIONALITY", "VALUE"),
                                *_all(node, addr_tag, "COUNTRY"),
                            ]
                        )
                    ),
                    identifiers=_identifiers(*_all(node, "INDIVIDUAL_DOCUMENT", "NUMBER")),
                    programs=tuple(p for p in (_t(node, "UN_LIST_TYPE"),) if p),
                )
            )
    if not out:
        raise ListFormatError("no designations found")
    return out


# ---- EU (unconfirmed) ------------------------------------------------------------------------------------------------


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _eu_attrs(node: Any, tag: str, attr: str) -> list[str]:
    return [
        c.attrib.get(attr, "").strip()
        for c in node
        if _local(c.tag) == tag and c.attrib.get(attr, "").strip()
    ]


def parse_eu_xml(raw: bytes) -> list[Entry]:
    try:
        root = ET.fromstring(raw)
    except (ET.ParseError, DefusedXmlException) as e:
        raise ListFormatError("not well-formed XML, or it uses entities") from e
    nodes = [n for n in root.iter() if _local(n.tag) == "sanctionEntity"]
    if not nodes:
        raise ListFormatError("no sanctionEntity elements")
    out: list[Entry] = []
    for node in nodes:
        ref = node.attrib.get("logicalId") or node.attrib.get("euReferenceNumber") or ""
        names = _eu_attrs(node, "nameAlias", "wholeName")
        if not ref or not names:
            continue
        subject = next((c for c in node if _local(c.tag) == "subjectType"), None)
        code = (subject.attrib.get("code", "") if subject is not None else "").casefold()
        kind = (
            PartyKind.PERSON
            if code == "person"
            else PartyKind.ENTITY
            if code in {"enterprise", "entity"}
            else PartyKind.OTHER
        )
        out.append(
            Entry(
                list_id=ListId.EU_CONSOLIDATED,
                source_ref=ref,
                kind=kind,
                primary_name=names[0],
                aliases=tuple(dict.fromkeys(n for n in names[1:] if n != names[0])),
                birth=_years(
                    " ".join(
                        _eu_attrs(node, "birthdate", "birthdate")
                        + _eu_attrs(node, "birthdate", "year")
                    )
                ),
                countries=tuple(
                    dict.fromkeys(
                        c.casefold() for c in _eu_attrs(node, "citizenship", "countryIso2Code")
                    )
                ),
                identifiers=_identifiers(*_eu_attrs(node, "identification", "number")),
                programs=tuple(dict.fromkeys(_eu_attrs(node, "regulation", "programme"))),
            )
        )
    if not out:
        raise ListFormatError("no designations found")
    return out


# ---- local lists (a documented normalized format) ---------------------------------------------------------------------

LOCAL_HEADER = (
    "list_key",
    "source_ref",
    "kind",
    "primary_name",
    "aliases",
    "birth",
    "countries",
    "identifiers",
    "programs",
)


def parse_local_csv(raw: bytes) -> list[Entry]:
    """A country's own sanctions list, converted by its owner to this format: one row per designation, the columns in
    `LOCAL_HEADER`, several values in one cell separated by `|`. `list_key` names the source list (for example `AE-LTL`) and
    prefixes the source reference so two lists in one file cannot collide."""
    rows = list(csv.reader(io.StringIO(_decode(raw))))
    if not rows or tuple(c.strip() for c in rows[0]) != LOCAL_HEADER:
        raise ListFormatError(f"the first row must be exactly: {','.join(LOCAL_HEADER)}")

    def many(value: str) -> tuple[str, ...]:
        return tuple(dict.fromkeys(p.strip() for p in value.split("|") if p.strip()))

    out: list[Entry] = []
    for n, row in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in row):
            continue
        if len(row) != len(LOCAL_HEADER):
            raise ListFormatError(f"row {n} has {len(row)} columns, not {len(LOCAL_HEADER)}")
        key, ref, kind, name, aliases, birth, countries, identifiers, programs = (
            c.strip() for c in row
        )
        if not key or not ref or not name or kind not in {"PERSON", "ENTITY", "OTHER"}:
            raise ListFormatError(
                f"row {n} needs list_key, source_ref, primary_name and a kind of PERSON, ENTITY or OTHER"
            )
        out.append(
            Entry(
                list_id=ListId.LOCAL,
                source_ref=f"{key}:{ref}",
                kind=PartyKind(kind),
                primary_name=name,
                aliases=many(aliases),
                birth=_years(" ".join(many(birth))),
                countries=tuple(c.casefold() for c in many(countries)),
                identifiers=_identifiers(*many(identifiers)),
                programs=many(programs) or (key,),
            )
        )
    if not out:
        raise ListFormatError("no designations found")
    return out


# ---- PEP -------------------------------------------------------------------------------------------------------------

PEP_HEADER = ("source_ref", "name", "aliases", "birth", "country", "position", "level")
PEP_LEVELS = frozenset(
    {
        "HEAD_OF_STATE",
        "NATIONAL",
        "REGIONAL",
        "LOCAL",
        "INTERNATIONAL_ORG",
        "STATE_ENTERPRISE",
        "RELATIVE",
        "ASSOCIATE",
    }
)


def _pep_entry(
    n: int,
    ref: str,
    name: str,
    aliases: list[str],
    birth: str,
    country: str,
    position: str,
    level: str,
) -> Entry:
    if not ref or not name or level not in PEP_LEVELS:
        raise ListFormatError(
            f"entry {n} needs source_ref, name and a level of {', '.join(sorted(PEP_LEVELS))}"
        )
    return Entry(
        list_id=ListId.PEP,
        source_ref=ref,
        kind=PartyKind.PERSON,
        primary_name=name,
        aliases=tuple(dict.fromkeys(a for a in aliases if a)),
        birth=_years(birth),
        countries=(country.casefold(),) if country else (),
        programs=(f"LEVEL:{level}", f"POSITION:{position}" if position else "POSITION:UNSPECIFIED"),
    )


def parse_pep(raw: bytes) -> list[Entry]:
    """The PEP feed in the platform's own documented shape (a licensed source is converted to it by its owner; ZID-GAP-001 WP-14).

    CSV: the first row exactly `PEP_HEADER` (source_ref,name,aliases,birth,country,position,level); several aliases in one cell are
    separated by `|`. JSON: `{"entries": [{"source_ref", "name", "aliases": [], "birth", "country", "position", "level"}]}`.
    `level` is one of `PEP_LEVELS`. Every entry is a PERSON; the position and level go to `programs` so a reviewer sees why the
    person is listed. A file that does not match is refused whole. Nothing here says a person is a PEP in law: it is a list entry."""
    text = _decode(raw)
    entries: list[Entry] = []
    if text.lstrip().startswith(("{", "[")):
        try:
            doc = json.loads(text)
        except ValueError as e:
            raise ListFormatError("not valid JSON") from e
        items = doc.get("entries") if isinstance(doc, dict) else doc
        if not isinstance(items, list):
            raise ListFormatError("expected a list of entries")
        for n, it in enumerate(items, start=1):
            if not isinstance(it, dict):
                raise ListFormatError(f"entry {n} is not an object")
            aliases = it.get("aliases") or []
            if not isinstance(aliases, list):
                raise ListFormatError(f"entry {n}: aliases must be a list")
            entries.append(
                _pep_entry(
                    n,
                    str(it.get("source_ref", "")).strip(),
                    str(it.get("name", "")).strip(),
                    [str(a).strip() for a in aliases],
                    str(it.get("birth", "") or ""),
                    str(it.get("country", "") or "").strip(),
                    str(it.get("position", "") or "").strip(),
                    str(it.get("level", "")).strip(),
                )
            )
    else:
        rows = list(csv.reader(io.StringIO(text)))
        if not rows or tuple(c.strip() for c in rows[0]) != PEP_HEADER:
            raise ListFormatError(f"the first row must be exactly: {','.join(PEP_HEADER)}")
        for n, row in enumerate(rows[1:], start=2):
            if not any(c.strip() for c in row):
                continue
            if len(row) != len(PEP_HEADER):
                raise ListFormatError(f"row {n} has {len(row)} columns, not {len(PEP_HEADER)}")
            ref, name, aliases, birth, country, position, level = (c.strip() for c in row)
            entries.append(
                _pep_entry(n, ref, name, aliases.split("|"), birth, country, position, level)
            )
    if not entries:
        raise ListFormatError("no entries found")
    if len({e.source_ref for e in entries}) != len(entries):
        raise ListFormatError("a source_ref appears twice")
    return entries


PARSERS: dict[ListId, Any] = {
    ListId.OFAC_SDN: parse_ofac,
    ListId.OFAC_NON_SDN: partial(parse_ofac, list_id=ListId.OFAC_NON_SDN),
    ListId.LOCAL: parse_local_csv,
    ListId.PEP: parse_pep,
    ListId.UK_SANCTIONS: parse_uk_csv,
    ListId.UN_SC: parse_un_xml,
    ListId.EU_CONSOLIDATED: parse_eu_xml,
}


def count(entries: Iterable[Entry]) -> int:
    return sum(1 for _ in entries)
