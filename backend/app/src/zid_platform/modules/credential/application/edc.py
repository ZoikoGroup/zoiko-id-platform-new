"""Europass European Digital Credential (EDC) mapping (ZID-GAP-001 v2.0 GID-051). Pure: bytes in, three claim fields out.

An EDC is an XML credential sealed with an XAdES e-seal. Validating the seal is the evidence module's job; this module only reads WHAT the
credential claims, and only the fields a relying party needs: who awarded it, what it is called, when it was issued. It never returns
anything about the holder (no name, no date of birth, no identifier): a verifier asking "is this seal good" does not get the person.

The mapping is by element name, ignoring namespaces, because the EDC data model is published in several versions. A field that is not
found is reported as missing, never filled in.
"""

from __future__ import annotations

from dataclasses import dataclass

from defusedxml import DefusedXmlException
from defusedxml import ElementTree as SafeET

MAX_TEXT = 300


class EdcError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True, slots=True)
class EdcClaim:
    awarding_body: str | None
    title: str | None
    issued: str | None

    def missing(self) -> list[str]:
        return [
            name
            for name, value in (
                ("awarding_body", self.awarding_body),
                ("title", self.title),
                ("issued", self.issued),
            )
            if value is None
        ]

    def as_dict(self) -> dict[str, str | None]:
        return {"awarding_body": self.awarding_body, "title": self.title, "issued": self.issued}


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _text(el: object) -> str | None:
    parts = [t.strip() for t in el.itertext() if t and t.strip()]  # type: ignore[attr-defined]
    value = " ".join(parts)
    return value[:MAX_TEXT] if value else None


def _first(root: object, name: str) -> object | None:
    return next((e for e in root.iter() if _local(e.tag) == name), None)  # type: ignore[attr-defined]


def _any(root: object, names: tuple[str, ...]) -> object | None:
    """The first of these elements found. An element with no children is falsy, so `or` must not be used to choose between them."""
    for name in names:
        found = _first(root, name)
        if found is not None:
            return found
    return None


def _within(root: object, parent: str, children: tuple[str, ...]) -> str | None:
    holder = _first(root, parent)
    if holder is None:
        return None
    for child in children:
        found = _first(holder, child)
        if found is not None and (value := _text(found)):
            return value
    return None


def is_edc(xml: bytes) -> bool:
    """A cheap look at the document element, so a caller can say "this is not an EDC" before anything else."""
    try:
        root = SafeET.fromstring(xml, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (DefusedXmlException, SafeET.ParseError, ValueError):
        return False
    return "europeandigitalcredential" in _local(root.tag).lower() or any(
        _local(e.tag) == "learningAchievement" for e in root.iter()
    )


def read(xml: bytes) -> EdcClaim:
    try:
        root = SafeET.fromstring(xml, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (DefusedXmlException, SafeET.ParseError, ValueError) as e:
        raise EdcError("EDC_NOT_READABLE") from e
    if not is_edc(xml):
        raise EdcError("NOT_AN_EDC")
    achievement = _first(root, "learningAchievement")
    body = achievement if achievement is not None else root
    title_el = _first(body, "title")
    issued_el = _any(root, ("issued", "issuanceDate", "validFrom"))
    return EdcClaim(
        awarding_body=_within(root, "issuer", ("legalName", "preferredName", "name"))
        or _within(root, "awardingBody", ("legalName", "preferredName", "name")),
        title=_text(title_el) if title_el is not None else None,
        issued=_text(issued_el) if issued_el is not None else None,
    )
